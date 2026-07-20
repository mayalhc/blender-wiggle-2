import bpy, math
from bpy.app.handlers import persistent
from mathutils import Vector, Matrix, Quaternion, noise
from . import wiggle_cache

ZERO = Vector((0, 0, 0))
#return m2 in m1 space
def relative_matrix(m1,m2):
    return (m2.inverted() @ m1).inverted()

def flatten(mat):
    dim = len(mat)
    return [mat[j][i] for i in range(dim) 
                      for j in range(dim)]

def reset_scene():
    for wo in bpy.context.scene.wiggle.list:
        reset_ob(bpy.data.objects.get(wo.name))
                              
def reset_ob(ob):
    if not ob:
        return
    # 1. Fetch the wiggle data for this object
    wo = bpy.context.scene.wiggle.list.get(ob.name)

    # 2. Make sure the data actually exists (is not None) first
    if wo and hasattr(wo, 'list'):
        arm_obj = bpy.data.objects.get(wo.name)
        if arm_obj and arm_obj.pose:
            bones = [arm_obj.pose.bones.get(wb.name) for wb in wo.list]
            reset_bones_batch(bones)
    else:
        # No data - silently skip without erroring
        pass


def reset_bone(b):
    """Reset a single bone. Calls view_layer.update() itself, so this is
    only for single-bone use - resetting many bones in a loop should use
    reset_bones_batch() instead (one view_layer.update() for the whole
    batch, not one per bone - the latter re-evaluates the full depsgraph
    once per bone and gets very slow on rigs with many wiggle bones)."""
    reset_bones_batch([b])


def reset_bones_batch(bones):
    bones = [b for b in bones if b is not None]
    if not bones:
        return
    clear_parent_cache()

    # 1. Reset every bone's visual pose back to its rest position
    for b in bones:
        b.matrix_basis = Matrix.Identity(4)
        b.id_data.update_tag()

    # 2. Recompute world matrices exactly once for the whole batch
    #    (calling this once per bone would re-evaluate the entire depsgraph
    #    that many times, which gets very slow on rigs with many bones)
    bpy.context.view_layer.update()

    zero_v = Vector((0, 0, 0))
    for b in bones:
        # 3. Grab each bone's now-updated real world position
        world_mat = b.id_data.matrix_world
        current_world_matrix = world_mat @ b.matrix

        # 4. Fully sync the physics coordinates to the bone's actual world position
        curr_tail_pos = (world_mat @ b.matrix @ Matrix.Translation(Vector((0, b.bone.length, 0)))).translation
        curr_head_pos = current_world_matrix.translation

        # 5. Completely zero out acceleration and velocity (prevents flying off)
        b.wiggle.position = b.wiggle.position_last = curr_tail_pos
        b.wiggle.position_head = b.wiggle.position_last_head = curr_head_pos

        b.wiggle.velocity = b.wiggle.velocity_head = zero_v
        b.wiggle.collision_normal = b.wiggle.collision_normal_head = zero_v

        # 6. Overwrite the physics matrix data with the freshly reset values too
        b.wiggle.matrix = flatten(current_world_matrix)

        # 7. Finally call update_matrix to finish preparing for simulation
        update_matrix(b, last=True)


                      
def build_list():
    bpy.context.scene.wiggle.list.clear()
    for ob in bpy.context.scene.objects:
        if ob.type != 'ARMATURE': continue
        wigglebones = []
        for b in ob.pose.bones:
            if b.wiggle_tail or (b.wiggle_head and not b.bone.use_connect):
                wigglebones.append(b)
                b.wiggle_enable = True
            else:
                b.wiggle_enable = False
#                continue
#            wigglebones.append(b)
                
        if not wigglebones:
            ob.wiggle_enable = False
            continue
        
        ob.wiggle_enable = True
        wo = bpy.context.scene.wiggle.list.add()
        wo.name = ob.name
        for b in wigglebones:
            wb = wo.list.add()
            wb.name = b.name


def update_prop(self, context, prop):
    """Propagate the same value to selected pose bones. Dropped the b[prop] approach in favor of setattr for consistency."""
    if context.scene.get("wiggle_updating"):
        return
    val = getattr(self, prop)
    context.scene["wiggle_updating"] = True
    try:
        if isinstance(self, bpy.types.PoseBone):
            selected = list(getattr(context, "selected_pose_bones", None) or [])
            # [Bug fix] This used to compare identity ("b is self"), but Blender
            # can hand back a different Python wrapper object for the same
            # bone between context.selected_pose_bones and the update
            # callback's self (the same issue was found/fixed in other
            # NLA/PropertyGroup-related code). That meant "self, which
            # already has the value set" wasn't recognized, so it got
            # setattr'd again - with multiple bones selected this produced
            # unpredictable ordering where values snapped back, or
            # reset_bone() never being called on the active bone, so the
            # result looked different from bone to bone. Switched to a
            # name comparison instead.
            for b in selected:
                if b.name == self.name:
                    continue
                try:
                    setattr(b, prop, val)
                except (AttributeError, TypeError):
                    pass
            if prop in ('wiggle_head', 'wiggle_tail', 'wiggle_enable', 'wiggle_mute'):
                rb = globals().get('reset_bone')
                if rb:
                    for b in selected:
                        try: rb(b)
                        except Exception: pass
                    if not any(b.name == self.name for b in selected):
                        try: rb(self)
                        except Exception: pass
                bl = globals().get('build_list')
                if bl:
                    try: bl()
                    except Exception: pass
    finally:
        context.scene["wiggle_updating"] = False

_GET_PARENT_CACHE = {}

# So the Sim Mix Layers' Layer Weight/Sim Mix sliders can refresh the preview
# instantly without actually changing the current frame, this stores, per
# bone, the (animation rotation, simulation rotation, rotation_mode,
# animation position, simulation position) computed on the last real frame.
# Key: (object name, bone name). Consumed by refresh_influence_blend().
_LAST_BLEND_CACHE = {}


def refresh_influence_blend(obj):
    """Without changing the current frame (i.e. without recomputing physics),
    re-blend the last-computed animation/simulation pose using the current
    wiggle_influence value and apply it to the viewport immediately. Called
    when the Sim Mix Layers' Layer Weight/Sim Mix slider is moved (the
    timeline stays put). Since it never touches physics or the frame, there
    are no side effects like resets or lost velocity."""
    if not obj or obj.type != 'ARMATURE':
        return
    changed = False
    for b in obj.pose.bones:
        cached = _LAST_BLEND_CACHE.get((obj.name, b.name))
        if not cached:
            continue
        anim_q, sim_q, rmode, anim_loc, sim_loc, anim_scale, sim_scale = cached
        inf = getattr(b, "wiggle_influence", 1.0)
        blended_q = anim_q.slerp(sim_q, inf)
        if rmode == 'QUATERNION':
            b.rotation_quaternion = blended_q
        elif rmode == 'AXIS_ANGLE':
            axis, angle = blended_q.to_axis_angle()
            b.rotation_axis_angle = (angle, axis.x, axis.y, axis.z)
        else:
            b.rotation_euler = blended_q.to_euler(rmode)
        b.location = anim_loc.lerp(sim_loc, inf)
        b.scale = anim_scale.lerp(sim_scale, inf)
        changed = True
    if changed and obj.id_data:
        obj.id_data.update_tag()


def clear_parent_cache():
    """get_parent()'s result can never change within a single frame (wiggle_tail/
    head/mute don't change mid-frame), but constrain() was calling it
    `iterations` times per bone and walking back up the parent chain to
    recompute every time. Clear the cache at the start of each frame and
    reuse it for the rest of that frame."""
    _GET_PARENT_CACHE.clear()

def get_parent(b):
    key = (b.id_data.name, b.name)
    cached = _GET_PARENT_CACHE.get(key, False)
    if cached is not False:
        return cached
    p = b.parent
    if not p:
        result = None
    else:
        result = p if (p.wiggle_enable and (not p.wiggle_mute) and ((p.wiggle_head and not p.bone.use_connect) or p.wiggle_tail)) else get_parent(p)
    _GET_PARENT_CACHE[key] = result
    return result

def length_world(b):
    return (b.id_data.matrix_world @ b.head - b.id_data.matrix_world @ b.tail).length

def collider_poll(self, object):
    # In analytic primitive (Sphere/Box/Cylinder/Capsule) mode no mesh is
    # needed, so allow any object (including Empties) to be used as a
    # transform reference.
    ctype = getattr(self, "wiggle_collider_type", 'Object')
    if ctype in PRIMITIVE_COLLIDERS:
        return True
    return object.type == 'MESH'

WIND_FIELD_TYPES = {'WIND', 'TURBULENCE', 'VORTEX'}

def wind_poll(self, object):
    # [Feature extension] Originally only the WIND type was allowed. Now Turbulence/Vortex are also supported.
    return object.field and object.field.type in WIND_FIELD_TYPES


def compute_wind_force(wind_ob, pos, ref_dir, mass, wind_mult):
    """Computes the actual force based on wind_ob's Force Field type (Wind/Turbulence/Vortex).
    Returned as acceleration (i.e. divided by mass, F/mass) so the caller can multiply by dt^2 directly."""
    field = wind_ob.field
    if not field:
        return Vector((0, 0, 0))
    strength = getattr(field, 'strength', 1.0)
    mass = max(mass, 0.0001)

    if field.type == 'WIND':
        w_dir = (wind_ob.matrix_world.to_quaternion() @ Vector((0, 0, 1))).normalized()
        fac = 1 - field.wind_factor * abs(w_dir.dot(ref_dir))
        return w_dir * fac * strength * wind_mult / mass

    elif field.type == 'TURBULENCE':
        size = max(getattr(field, 'size', 1.0), 0.0001)
        freq = 1.0 / size
        # There's no 4D noise available, so offset the Z axis by the frame
        # number as an approximation, letting the force fluctuate over time
        # even for a stationary bone (mimicking a real Turbulence field).
        frame = bpy.context.scene.frame_current
        sample_pos = pos * freq + Vector((0.0, 0.0, frame * 0.1))
        noise_vec = noise.noise_vector(sample_pos)
        return noise_vec * strength * wind_mult / mass

    elif field.type == 'VORTEX':
        obj_mat = wind_ob.matrix_world
        obj_mat_inv = obj_mat.inverted_safe()
        local_pos = obj_mat_inv @ pos
        radial = Vector((local_pos.x, local_pos.y, 0.0))
        dist = radial.length
        if dist < 1e-6:
            return Vector((0, 0, 0))
        tangent_local = Vector((-radial.y, radial.x, 0.0)).normalized()
        radial_local = radial.normalized()
        inflow = getattr(field, 'inflow', 0.0)
        combined_local = tangent_local - radial_local * inflow
        combined_world = (obj_mat.to_3x3() @ combined_local).normalized()
        falloff = 1.0 / max(dist, 0.1)
        return combined_world * strength * wind_mult * falloff / mass

    return Vector((0, 0, 0))


# ============================================================
# Analytic primitive colliders (Sphere/Box/Cylinder/Capsule)
# Collision is determined purely from the object's transform (position/
# rotation/scale), with no mesh involved.
# Everything is computed against a "unit primitive" and the object scale
# provides the actual size (matching Blender's default primitive size
# conventions):
#   Sphere   : radius 1
#   Box      : half-extent 1 (default Cube, 2x2x2)
#   Cylinder : radius 1, half-height 1, along Z axis (default Cylinder, radius 1 height 2)
#   Capsule  : radius 1, body half-height 1, along Z axis
# Each function takes a point in the object's local space and returns
# (local closest point, local normal).
# ============================================================

def _closest_point_sphere(lp):
    d = lp.length
    if d < 1e-8:
        return Vector((0, 0, 1)), Vector((0, 0, 1))
    n = lp.normalized()
    return n, n

def _closest_point_box(lp):
    cx = max(-1.0, min(1.0, lp.x))
    cy = max(-1.0, min(1.0, lp.y))
    cz = max(-1.0, min(1.0, lp.z))
    inside = abs(lp.x) <= 1.0 and abs(lp.y) <= 1.0 and abs(lp.z) <= 1.0
    if inside:
        # If inside, push out toward the nearest face
        faces = [
            (1.0 - abs(lp.x), Vector((1.0 if lp.x >= 0 else -1.0, 0.0, 0.0))),
            (1.0 - abs(lp.y), Vector((0.0, 1.0 if lp.y >= 0 else -1.0, 0.0))),
            (1.0 - abs(lp.z), Vector((0.0, 0.0, 1.0 if lp.z >= 0 else -1.0))),
        ]
        faces.sort(key=lambda t: t[0])
        n = faces[0][1]
        closest = Vector((cx, cy, cz))
        if n.x: closest.x = n.x
        elif n.y: closest.y = n.y
        else: closest.z = n.z
        return closest, n
    else:
        closest = Vector((cx, cy, cz))
        d = lp - closest
        n = d.normalized() if d.length > 1e-8 else Vector((0, 0, 1))
        return closest, n

def _closest_point_cylinder(lp):
    radial = Vector((lp.x, lp.y, 0.0))
    r = radial.length
    radial_dir = radial.normalized() if r > 1e-8 else Vector((1.0, 0.0, 0.0))
    inside = (r <= 1.0) and (abs(lp.z) <= 1.0)
    if inside:
        if (1.0 - r) < (1.0 - abs(lp.z)):
            closest = radial_dir + Vector((0.0, 0.0, lp.z))
            n = radial_dir
        else:
            cap_z = 1.0 if lp.z >= 0 else -1.0
            closest = Vector((lp.x, lp.y, cap_z))
            n = Vector((0.0, 0.0, cap_z))
        return closest, n
    else:
        if r > 1.0 and abs(lp.z) <= 1.0:
            closest = radial_dir + Vector((0.0, 0.0, lp.z))
            n = radial_dir
        else:
            cap_z = 1.0 if lp.z > 1.0 else (-1.0 if lp.z < -1.0 else lp.z)
            cr = min(r, 1.0)
            closest = radial_dir * cr + Vector((0.0, 0.0, cap_z))
            n = Vector((0.0, 0.0, 1.0 if cap_z > 0 else -1.0))
        return closest, n

def _closest_point_capsule(lp):
    h = 1.0
    seg_z = max(-h, min(h, lp.z))
    seg = Vector((0.0, 0.0, seg_z))
    d = lp - seg
    n = d.normalized() if d.length > 1e-8 else Vector((1.0, 0.0, 0.0))
    return seg + n, n

PRIMITIVE_COLLIDERS = {
    'Sphere': _closest_point_sphere,
    'Box': _closest_point_box,
    'Cylinder': _closest_point_cylinder,
    'Capsule': _closest_point_capsule,
}


# ============================================================
# Self collision (opt-in, per object) - true capsule(segment)-capsule collision.
# Each wiggle_tail bone is treated as a head-tail segment (radius
# wiggle_radius), and the actual distance is computed with the standard
# "closest point between two 3D segments" algorithm (Ericson, Real-Time
# Collision Detection). More accurate than a point-based check - for
# example it also catches two bones grazing past each other in their
# middle sections.
# Directly-connected parent-child pairs are excluded from the check since
# they're meant to be touching - otherwise they'd constantly fight the
# stiffness/stretch constraints and cause jitter.
# Applied exactly once per frame (not per iteration) to minimize
# oscillation risk.
# Note: a "floating point" bone with only wiggle_head enabled and
# wiggle_tail off can't form a segment, so it's excluded from this check
# (a rare case).
# ============================================================

def _closest_seg_seg(p1, q1, p2, q2):
    """Returns the closest point pair and parameters (s,t, 0=start point,
    1=end point) between two 3D segments (p1-q1, p2-q2). Standard algorithm."""
    d1 = q1 - p1
    d2 = q2 - p2
    r = p1 - p2
    a = d1.dot(d1)
    e = d2.dot(d2)
    f = d2.dot(r)

    if a <= 1e-9 and e <= 1e-9:
        return p1, p2, 0.0, 0.0

    if a <= 1e-9:
        s = 0.0
        t = max(0.0, min(1.0, f / e)) if e > 1e-9 else 0.0
    else:
        c = d1.dot(r)
        if e <= 1e-9:
            t = 0.0
            s = max(0.0, min(1.0, -c / a))
        else:
            b_ = d1.dot(d2)
            denom = a * e - b_ * b_
            s = max(0.0, min(1.0, (b_ * f - c * e) / denom)) if abs(denom) > 1e-9 else 0.0
            t = (b_ * s + f) / e
            if t < 0.0:
                t = 0.0
                s = max(0.0, min(1.0, -c / a))
            elif t > 1.0:
                t = 1.0
                s = max(0.0, min(1.0, (b_ - c) / a))

    c1 = p1 + d1 * s
    c2 = p2 + d2 * t
    return c1, c2, s, t


def _capsule_endpoints(b):
    """World-space head/tail coordinates when treating the bone as a
    capsule (segment). If wiggle_head is on, uses the simulated head
    position; otherwise uses the current animated (fixed) head position."""
    tail = b.wiggle.position
    if b.wiggle_head and not b.bone.use_connect:
        head = b.wiggle.position_head
    else:
        head = b.id_data.matrix_world @ b.head
    return head, tail


def apply_self_collision(active_bones, margin=0.0):
    tail_bones = [b for b in active_bones if b.wiggle_tail]
    n = len(tail_bones)
    if n < 2:
        return

    # Precompute the set of directly-connected (parent-child) bone pairs to skip
    adjacent = set()
    for b in active_bones:
        p = get_parent(b)
        if p:
            adjacent.add((b.name, p.name))
            adjacent.add((p.name, b.name))

    for i in range(n):
        b1 = tail_bones[i]
        r1 = b1.wiggle_radius
        head1, tail1 = _capsule_endpoints(b1)
        floating1 = b1.wiggle_head and not b1.bone.use_connect

        for j in range(i + 1, n):
            b2 = tail_bones[j]
            if (b1.name, b2.name) in adjacent:
                continue

            r2 = b2.wiggle_radius
            head2, tail2 = _capsule_endpoints(b2)
            floating2 = b2.wiggle_head and not b2.bone.use_connect

            c1, c2, s, t = _closest_seg_seg(head1, tail1, head2, tail2)
            diff = c2 - c1
            dist = diff.length
            min_dist = r1 + r2 + margin
            if not (1e-6 < dist < min_dist):
                continue

            push = diff.normalized() * ((min_dist - dist) * 0.5)

            # s/t: 0=head side, 1=tail side - push the endpoint on the colliding
            # side by that proportion. If head is animation-fixed
            # (non-floating) it can't be moved, so apply the full correction
            # to tail instead.
            if floating1:
                b1.wiggle.position_head -= push * (1.0 - s)
                b1.wiggle.position -= push * s
            else:
                b1.wiggle.position -= push

            if floating2:
                b2.wiggle.position_head += push * (1.0 - t)
                b2.wiggle.position += push * t
            else:
                b2.wiggle.position += push


def collide(b,dg,head=False,register_bounce=False):
    dt = bpy.context.scene.wiggle.dt

    if head:
        pos = b.wiggle.position_head
        vel = b.wiggle.velocity_head
        cp = b.wiggle.collision_point_head
        co = b.wiggle.collision_ob_head
        cn = b.wiggle.collision_normal_head
        
        collider_type = b.wiggle_collider_type_head
        wiggle_collider = b.wiggle_collider_head
        wiggle_collection = b.wiggle_collider_collection_head
        
        radius = b.wiggle_radius_head
        sticky = b.wiggle_sticky_head
        bounce = b.wiggle_bounce_head
        friction = b.wiggle_friction_head
    else:
        pos = b.wiggle.position
        vel = b.wiggle.velocity
        cp = b.wiggle.collision_point
        co = b.wiggle.collision_ob
        cn = b.wiggle.collision_normal
        
        collider_type = b.wiggle_collider_type
        wiggle_collider = b.wiggle_collider
        wiggle_collection = b.wiggle_collider_collection
        
        radius = b.wiggle_radius
        sticky = b.wiggle_sticky
        bounce = b.wiggle_bounce
        friction = b.wiggle_friction
        
    colliders = []
    if collider_type == 'Object' and wiggle_collider:
        if wiggle_collider.name in bpy.context.scene.objects:
            colliders = [wiggle_collider]
    if collider_type == 'Collection' and wiggle_collection:
        if wiggle_collection and wiggle_collection.name in bpy.data.collections:
            colliders = [ob for ob in wiggle_collection.objects if ob.type == 'MESH']
    if collider_type in PRIMITIVE_COLLIDERS and wiggle_collider:
        if wiggle_collider.name in bpy.context.scene.objects:
            colliders = [wiggle_collider]
    col = False
    for collider in colliders:
        cmw = collider.matrix_world
        cmw_inv = cmw.inverted_safe()

        if collider_type in PRIMITIVE_COLLIDERS:
            local_pos = cmw_inv @ pos
            local_closest, local_n = PRIMITIVE_COLLIDERS[collider_type](local_pos)
            i = cmw @ local_closest
            n = (cmw.to_quaternion().to_matrix().to_4x4() @ local_n).normalized()
        else:
            hit = collider.closest_point_on_mesh(cmw_inv @ pos, depsgraph=dg)
            if not hit or hit[0] is False:
                continue
            n = (cmw.to_quaternion().to_matrix().to_4x4() @ hit[2]).normalized()
            i = cmw @ hit[1]

        v = i-pos

        if (n.dot(v.normalized()) > 0.01) or (v.length < radius) or (co and (v.length < (radius+sticky))):
            if n.dot(v.normalized()) > 0: #vec is below
                nv = v.normalized()
            else: #normal is opposite dir to vec
                nv = -v.normalized()
            pos = i + nv*radius

            # [Bug fix] bounce/vel were only ever extracted and never used,
            # so the "Bounce" setting had no effect at all from the start.
            # Now the velocity component along the collision normal is
            # actually reflected (the amount of bounce is controlled by the
            # bounce value).
            vel_along_normal = vel.dot(nv)
            if vel_along_normal < 0:
                vel = vel - nv * (vel_along_normal * (1.0 + bounce))

            if co:
                collision_point = co.matrix_world @ cp
                pos = pos.lerp(collision_point, friction) # min(1,friction*60*dt))
            col = True
            co = collider
            cp = relative_matrix(cmw, Matrix.Translation(pos)).translation
            cn = nv
    if not col:
        co = None
#        cp = cn = Vector((0,0,0))

    if head:
        b.wiggle.position_head = pos
        b.wiggle.collision_point_head = cp
        b.wiggle.collision_ob_head = co
        b.wiggle.collision_normal_head = cn
    else:
        b.wiggle.position = pos
        b.wiggle.collision_point = cp
        b.wiggle.collision_ob = co
        b.wiggle.collision_normal = cn

    # [Bug fix, continued] Because this system recomputes velocity at the
    # end of every frame from the difference between position and
    # position_last (verlet-style), directly changing vel got overwritten
    # by the end of the frame and had no effect. For the reflection to
    # actually carry into the next frame, position_last needs to be
    # adjusted too. Only applied when called from move() (register_bounce=
    # True) - if this were also applied on every constrain() iteration call,
    # the reflection would stack up each iteration and could become
    # unstable, so it's only reflected once per frame, at the real
    # collision (move step).
    if register_bounce and col:
        if head:
            b.wiggle.position_last_head = pos - vel
        else:
            b.wiggle.position_last = pos - vel

# START OF REVISION #
def update_matrix(b, last=False):
    loc = Matrix.Translation(Vector((0,0,0)))
    p = get_parent(b)
    if p:
        mat = p.wiggle.matrix @ relative_matrix(p.matrix, b.matrix)
        if b.bone.inherit_scale == 'FULL':
            m2 = mat
        else:
            diff = relative_matrix(p.matrix, b.matrix)
            lo = Matrix.Translation((p.wiggle.matrix @ diff).translation)
            ro = p.wiggle.matrix.to_quaternion().to_matrix().to_4x4() @ diff.to_quaternion().to_matrix().to_4x4()
            # Blender 5.0 compatibility: prevent scale contamination
            sc_vec = (b.id_data.matrix_world @ b.matrix).decompose()[2]
            sc = Matrix.LocRotScale(None, None, sc_vec)
            m2 = lo @ ro @ sc
    else:
        mat = b.id_data.matrix_world @ b.matrix
        m2 = mat
            
    if b.wiggle_head and not b.bone.use_connect:
        m2 = Matrix.Translation(b.wiggle.position_head - m2.translation) @ m2
        loc = Matrix.Translation(relative_matrix(mat, Matrix.Translation(b.wiggle.position_head)).translation)
        mat = m2
        
    v_rel = relative_matrix(m2, Matrix.Translation(b.wiggle.position)).translation
    rxz = v_rel.to_track_quat('Y','Z')
    rot = rxz.to_matrix().to_4x4()
    
    # --- Compute scale (sy) ---
    l_world = length_world(b)
    if b.bone.inherit_scale == 'FULL':
        l0 = b.bone.length
        l1 = v_rel.length
        sy = l1 / l0 if l0 > 0.0001 else 1.0
    else:
        par = b.parent
        if par:
            dist = ((b.id_data.matrix_world @ par.matrix @ relative_matrix(par.matrix, b.matrix)).translation - b.wiggle.position).length
            if p:
                dist = (p.wiggle.matrix @ relative_matrix(p.matrix, b.matrix).translation - b.wiggle.position).length
            sy = dist / l_world if l_world > 0.0001 else 1.0
        else:
            # Updated code:
            dist = ((b.id_data.matrix_world @ b.matrix).to_translation() - b.wiggle.position).length
            sy = dist / l_world if l_world > 0.0001 else 1.0
    
    if b.wiggle_head and not b.bone.use_connect:
        dist = (b.wiggle.position_head - b.wiggle.position).length
        sy = dist / l_world if l_world > 0.0001 else 1.0
            
    # [Key fix] Cut off visual stretching at the source when the Stretch
    # setting is 0 (or very small). Instead of clamping to 0.999~1.001,
    # just force sy to 1.0 outright whenever Stretch isn't being used.
    if hasattr(b, 'wiggle_stretch') and b.wiggle_stretch < 0.01:
        sy = 1.0
    else:
        # Safety net to prevent abnormal divergence (getting too long) even when Stretch is in use
        sy = max(0.1, min(10.0, sy))

    scale = Matrix.Scale(sy, 4, Vector((0, 1, 0)))
    
    if last:
        const = False
        for c in b.constraints:
            if c.enabled:
                const = True 
        if const:
            b.matrix = b.bone.matrix_local @ b.matrix_basis @ loc @ rot @ scale
        else:
            b.matrix = b.matrix @ loc @ rot @ scale
            
    # Normalize the final matrix and prevent scale contamination
    final_mat = m2 @ rot @ scale
    b.wiggle.matrix = flatten(final_mat)


def get_pin(b):
    for c in b.constraints:
        if c.type in ['DAMPED_TRACK','TRACK_TO','LOCKED_TRACK'] and c.target and not c.mute:
            return c
    return None

def pin(b):
    c = get_pin(b)
    if c:
        goal = c.target.matrix_world
        if c.subtarget:
            if c.subtarget in c.target.pose.bones:
                goal = goal @ c.target.pose.bones[c.subtarget].matrix
        # Blender 5.0 compatibility: numerical stability when computing Influence
        b.wiggle.position = b.wiggle.position * (1 - c.influence) + goal.translation * c.influence
# END OF REVISION #


# START OF REVISION #
def update_visual_guide(self, context):
    if 'update_prop' in globals():
        update_prop(self, context, 'wiggle_radius')
        
    guide_obj = bpy.data.objects.get(f"WGuide_{self.name}")
    if guide_obj and guide_obj.type == 'MESH':
        length = self.bone.length
        t = self.wiggle_radius * 2 if self.wiggle_radius > 0.0005 else 0.001
        
        old_offset = guide_obj.get("last_offset", length / t)
        new_offset = length / t
        guide_obj.scale = (t, t, t)
        
        # Blender's standard approach for safely updating mesh data
        mesh = guide_obj.data
        is_capsule = len(mesh.vertices) > 100
        
        for v in mesh.vertices:
            if is_capsule:
                if v.co.z > 0.6:
                    v.co.z = (v.co.z - old_offset) + new_offset
            else:
                if v.co.z > 0.1:
                    v.co.z = new_offset
                    
        guide_obj["last_offset"] = new_offset
        mesh.update()  # Tell Blender the mesh has changed so the viewport refreshes

# END OF REVISION #

# START OF REVISION #

# ====================== 1. apply_angle_limits (fully stable version - unchanged) ======================
def apply_angle_limits(b, h_pos, q_basis, rest_dir, world_rest_length):
    from math import atan2, cos, sin, radians, acos
    from mathutils import Vector, Quaternion

    curr_vec = b.wiggle.position - h_pos
    if curr_vec.length < 1e-6:
        return h_pos + rest_dir * world_rest_length

    wiggle_angle_limit = getattr(b, "wiggle_angle_limit", 180)
    if wiggle_angle_limit < 179.5:
        limit_rad = radians(wiggle_angle_limit)
        
        # 1. Safely normalize both vectors.
        rest_dir_norm = rest_dir.normalized()
        curr_dir = curr_vec.normalized()

        # 2. Clamp to avoid floating-point error
        dot = max(-1.0, min(1.0, rest_dir_norm.dot(curr_dir)))
        angle = acos(dot)
        
        if angle > limit_rad:
            axis = rest_dir_norm.cross(curr_dir)
            
            if axis.length > 1e-6:
                axis.normalize()
                curr_dir = Quaternion(axis, limit_rad) @ rest_dir_norm
            else:

                ortho_axis = Vector((0, 1, 0)) if abs(rest_dir_norm.x) > 0.9 else Vector((1, 0, 0))
                
                axis = rest_dir_norm.cross(ortho_axis).normalized()
                curr_dir = Quaternion(axis, limit_rad) @ rest_dir_norm
            curr_vec = curr_dir * world_rest_length

    # =========================
    # 2. Individual limits (X / Z) - applied after the Cone
    # =========================
    if getattr(b, "wiggle_use_individual_limits", False):
        local_dir = q_basis.inverted() @ curr_vec

        ang_z = atan2(local_dir.x, local_dir.y)
        ang_x = -atan2(local_dir.z, local_dir.y)

        lim_x = radians(b.wiggle_limit_x)
        lim_z = radians(b.wiggle_limit_z)

        ang_x_c = max(min(ang_x, lim_x), -lim_x)
        ang_z_c = max(min(ang_z, lim_z), -lim_z)

        new_l = Vector((
            sin(ang_z_c) * cos(ang_x_c),
            cos(ang_z_c) * cos(ang_x_c),
            -sin(ang_x_c)
        ))

        curr_vec = q_basis @ new_l * world_rest_length

    return h_pos + curr_vec


def reclamp_angle_limit(b):
    """apply_angle_limits() is only called from within move(), but the
    iterative distance-constraint solver in constrain() that runs after
    move() knows nothing about angle limits, so it can drag the clamped
    position back out on every iteration (e.g. a bug where a Total Limit
    of 10 degrees would actually end up spread as wide as 90 degrees).
    This re-clamps the final position once per frame, after all of
    constrain()'s iterations are done, so the angle limit is actually
    respected. Does not touch constrain()'s internal logic."""
    if not b.wiggle_tail:
        return
    limit = getattr(b, "wiggle_angle_limit", 180.0)
    if limit >= 179.5:
        return

    m_world = b.id_data.matrix_world
    p = b.bone.parent
    if p:
        pb_p = b.id_data.pose.bones.get(p.name)
        m_rest = m_world @ pb_p.matrix @ (p.matrix_local.inverted() @ b.bone.matrix_local)
    else:
        m_rest = m_world @ b.bone.matrix_local

    q_basis = m_rest.to_quaternion()
    world_rest_vec = m_rest.to_3x3() @ Vector((0, b.bone.length, 0))
    world_rest_length = world_rest_vec.length
    rest_dir = world_rest_vec.normalized() if world_rest_length > 1e-8 else Vector((0, 1, 0))

    h_pos = b.wiggle.position_head if (b.wiggle_head and not b.bone.use_connect) else (m_world @ b.head)

    b.wiggle.position = apply_angle_limits(b, h_pos, q_basis, rest_dir, world_rest_length)


def move(b, dg):

    dt = bpy.context.scene.wiggle.dt
    if not dt:
        if b.wiggle_tail or b.wiggle_head:
            m_world = b.id_data.matrix_world
            p = b.bone.parent
            if p:
                pb_p = b.id_data.pose.bones.get(p.name)
                m_rest = m_world @ pb_p.matrix @ (p.matrix_local.inverted() @ b.bone.matrix_local)
            else:
                m_rest = m_world @ b.bone.matrix_local
                
            q_basis = m_rest.to_quaternion()
            local_rest_vec = Vector((0, b.bone.length, 0))
            world_rest_vec = m_rest.to_3x3() @ local_rest_vec
            world_rest_length = world_rest_vec.length
            rest_dir = world_rest_vec.normalized() if world_rest_length > 1e-8 else Vector((0, 1, 0))
            
            h_pos_anim = m_world @ b.head
            
            if b.wiggle_head and not b.bone.use_connect:
                b.wiggle.position_head = h_pos_anim
            
            h_pos = b.wiggle.position_head if (b.wiggle_head and not b.bone.use_connect) else h_pos_anim
            
            if b.wiggle_tail:
                b.wiggle.position = h_pos + rest_dir * world_rest_length
            
            update_matrix(b, True)
        return

    # ================================
    # ↓↓↓ Actual simulation (dt > 0) ↓↓↓
    # ================================
    dt2 = dt * dt

    m_world = b.id_data.matrix_world
    p = b.bone.parent
    if p:
        pb_p = b.id_data.pose.bones.get(p.name)
        m_rest = m_world @ pb_p.matrix @ (p.matrix_local.inverted() @ b.bone.matrix_local)
    else:
        m_rest = m_world @ b.bone.matrix_local
        
    q_basis = m_rest.to_quaternion()
    world_rest_vec = m_rest.to_3x3() @ Vector((0, b.bone.length, 0))
    world_rest_length = world_rest_vec.length
    rest_dir = world_rest_vec.normalized() if world_rest_length > 1e-8 else Vector((0, 1, 0))
    
    h_pos_anim = m_world @ b.head

    # [Bug fix] wiggle_tail_mute/wiggle_head_mute were only registered and
    # never read anywhere, so they weren't even in the UI and had no
    # effect. When enabled, physics is now skipped and the bone just
    # follows its animated rest position (the bone isn't fully removed
    # from the chain, only force computation on that side is stopped).
    if b.wiggle_tail and getattr(b, 'wiggle_tail_mute', False):
        h_pos = b.wiggle.position_head if (b.wiggle_head and not b.bone.use_connect) else h_pos_anim
        b.wiggle.position = h_pos + rest_dir * world_rest_length
        b.wiggle.velocity = Vector((0, 0, 0))
        b.wiggle.position = apply_angle_limits(b, h_pos, q_basis, rest_dir, world_rest_length)
        pin(b)
        collide(b, dg, register_bounce=True)
    elif b.wiggle_tail:
        old_pos = b.wiggle.position.copy()

        # [Bug fix] adaptive_damp_mod was computed every frame but never
        # read anywhere, so the Safety Guard actually had no effect at all
        # (never factored into damping). Now it's added into the actual
        # damping calculation.
        extra_damp = getattr(b.wiggle, "adaptive_damp_mod", 0.0)
        damp = max(min(1 - (b.wiggle_damp + extra_damp) * dt, 1), 0)
        b.wiggle.velocity *= damp
        
        F = bpy.context.scene.gravity * b.wiggle_gravity
        if b.wiggle_wind_ob and b.wiggle_wind_ob.field:
            ref_dir = (b.wiggle.position - b.wiggle.matrix.translation)
            ref_dir = ref_dir.normalized() if ref_dir.length > 1e-8 else Vector((0, 0, 1))
            F += compute_wind_force(b.wiggle_wind_ob, b.wiggle.position, ref_dir, b.wiggle_mass, b.wiggle_wind)

        h_pos = b.wiggle.position_head if (b.wiggle_head and not b.bone.use_connect) else h_pos_anim
        target_pos = h_pos + rest_dir * world_rest_length
        dist_vec = target_pos - b.wiggle.position
        
        stiff_clamped = min(b.wiggle_stiff, 1.0 / dt2 * 0.1) 
        F += dist_vec * stiff_clamped

        b.wiggle.position += b.wiggle.velocity + F * dt2
        
        b.wiggle.position = apply_angle_limits(b, h_pos, q_basis, rest_dir, world_rest_length)
        
        pin(b)
        collide(b, dg, register_bounce=True)

        calculated_vel = (b.wiggle.position - old_pos)
        b.wiggle.velocity = b.wiggle.velocity * 0.2 + calculated_vel * 0.8

    if b.wiggle_head and not b.bone.use_connect and getattr(b, 'wiggle_head_mute', False):
        b.wiggle.position_head = h_pos_anim
        b.wiggle.velocity_head = Vector((0, 0, 0))
        collide(b, dg, True, register_bounce=True)
    elif b.wiggle_head and not b.bone.use_connect:
        old_h_pos = b.wiggle.position_head.copy()

        extra_damp_h = getattr(b.wiggle, "adaptive_damp_mod", 0.0)
        damp_h = max(min(1 - (b.wiggle_damp_head + extra_damp_h) * dt, 1), 0)
        b.wiggle.velocity_head *= damp_h
        
        F_h = bpy.context.scene.gravity * b.wiggle_gravity_head
        # [Bug fix] wiggle_wind_ob_head/wiggle_wind_head were registered and
        # exposed in the UI, but never actually applied here (only Tail wind worked).
        if b.wiggle_wind_ob_head and b.wiggle_wind_ob_head.field:
            ref_dir_h = (b.wiggle.position_head - b.wiggle.matrix.translation)
            ref_dir_h = ref_dir_h.normalized() if ref_dir_h.length > 1e-8 else Vector((0, 0, 1))
            F_h += compute_wind_force(b.wiggle_wind_ob_head, b.wiggle.position_head, ref_dir_h,
                                       b.wiggle_mass_head, b.wiggle_wind_head)
        stiff_h_clamped = min(b.wiggle_stiff_head, 1.0 / dt2 * 0.1)
        F_h += (h_pos_anim - b.wiggle.position_head) * stiff_h_clamped

        b.wiggle.position_head += b.wiggle.velocity_head + F_h * dt2

        collide(b, dg, True, register_bounce=True)

        # Max Offset: clamp how far the floating head may drift from its
        # animated rest position. 0 = unlimited (matches original behavior).
        max_offset = getattr(b, 'wiggle_max_offset_head', 0.0)
        if max_offset > 0.0001:
            d = b.wiggle.position_head - h_pos_anim
            if d.length > max_offset:
                b.wiggle.position_head = h_pos_anim + d.normalized() * max_offset

        b.wiggle.velocity_head = b.wiggle.velocity_head * 0.2 + (b.wiggle.position_head - old_h_pos) * 0.8
    else:
        b.wiggle.position_head = h_pos_anim
            
    update_matrix(b, True)





# START OF REVISION #
def constrain(b, i, dg, dt=None, iterations=None):
    # [Performance] dt/iterations don't change within a frame, but this
    # function was re-reading bpy.context.scene.wiggle every time it got
    # called (iterations times per bone). The caller (wiggle_post) now
    # reads it once and passes it down for reuse; if called elsewhere
    # without arguments it still falls back safely as before.
    if dt is None:
        dt = bpy.context.scene.wiggle.dt
    if iterations is None:
        iterations = bpy.context.scene.wiggle.iterations

    def get_fac(mass1, mass2):
        return 0.5 if mass1 == mass2 else mass1 / (mass1 + mass2)
    def spring(target, position, stiff):
        s = target - position
        Fs = s * stiff / iterations
        if (Fs * dt * dt).length > s.length:
            return s
        return Fs * dt
    
    def stretch(target, position, fac):
        s = target - position
        return s * (1 - fac)

    if dt:
        p = get_parent(b)
        if p:
            mat = p.wiggle.matrix @ relative_matrix(p.matrix, b.matrix)
        else:
            mat = b.id_data.matrix_world @ b.matrix
        update_p = False  
        
        # spring
        if b.wiggle_head and not b.bone.use_connect:
            target = mat.translation
            s = spring(target, b.wiggle.position_head, b.wiggle_stiff_head)
            if p and b.wiggle_chain_head:
                if p.wiggle_tail:
                    fac = get_fac(b.wiggle_mass_head, p.wiggle_mass) if i else p.wiggle_stretch
                    p.wiggle.position -= s * fac
                else:
                    fac = get_fac(b.wiggle_mass_head, p.wiggle_mass_head)
                    p.wiggle.position_head -= s * fac
                b.wiggle.position_head += s * (1 - fac)
            else:
                b.wiggle.position_head += s
            # [Key fix] Blender 5.0 scale runaway prevention: force scale to (1,1,1) unconditionally
            loc, rot, _ = mat.decompose()
            mat = Matrix.LocRotScale(b.wiggle.position_head, rot, Vector((1.0, 1.0, 1.0)))
            target = mat @ Vector((0, b.bone.length, 0))
            
            if b.wiggle_tail:
                s = spring(target, b.wiggle.position, b.wiggle_stiff)
                if b.wiggle_chain:
                    fac = get_fac(b.wiggle_mass, b.wiggle_mass_head)
                    b.wiggle.position_head -= s * fac
                    b.wiggle.position += s * (1 - fac)
                else:
                    b.wiggle.position += s
            else:
                b.wiggle.position = target
        else:
            # [Key fix] Use Vector((1,1,1)) instead of b.matrix.decompose()[2]
            loc, rot, _ = mat.decompose()
            mat = Matrix.LocRotScale(loc, rot, Vector((1.0, 1.0, 1.0)))
            target = mat @ Vector((0, b.bone.length, 0))
            s = spring(target, b.wiggle.position, b.wiggle_stiff)
            
            if p and b.wiggle_chain and p.wiggle_tail:
                fac = get_fac(b.wiggle_mass, p.wiggle_mass)
                if get_pin(b): fac = 1 - b.wiggle_stretch
                if i == 0: fac = p.wiggle_stretch
                if p == b.parent and b.bone.use_connect:
                    p.wiggle.position -= s * fac
                else:
                    tailpos = b.wiggle.matrix @ Vector((0, b.bone.length, 0))
                    midpos = (b.wiggle.matrix.translation + tailpos) / 2
                    v1 = midpos - p.wiggle.matrix.translation
                    tailpos -= s * fac
                    midpos = (b.wiggle.matrix.translation + tailpos) / 2
                    v2 = midpos - p.wiggle.matrix.translation
                    
                    v1_len = v1.length
                    if v1_len > 0.0001:
                        sc = v2.length / v1_len
                        # Prevent squashing: clamp sc (scale) so it doesn't stray too far from 1
                        sc = max(0.99, min(1.01, sc))
                        q = v1.rotation_difference(v2)
                        v3 = q @ (p.wiggle.position - p.wiggle.matrix.translation)
                        p.wiggle.position = p.wiggle.matrix.translation + v3 * sc
                    
                b.wiggle.position += s * (1 - fac)
                update_p = True
            else:
                b.wiggle.position += s
                
        # stretch
        if b.wiggle_head and not b.bone.use_connect:
            if p:
                if b.parent == p and p.wiggle_tail:
                    # Extra length normalization
                    v_dir = (b.wiggle.position_head - p.wiggle.position).normalized()
                    target = p.wiggle.position + v_dir * (b.id_data.matrix_world @ b.head - b.id_data.matrix_world @ p.tail).length
                else:
                    targetpos = p.wiggle.matrix @ relative_matrix(p.matrix, b.parent.matrix) @ Vector((0, b.parent.length, 0))
                    v_dir = (b.wiggle.position_head - targetpos).normalized()
                    target = targetpos + v_dir * (b.id_data.matrix_world @ b.head - b.id_data.matrix_world @ b.parent.tail).length
            elif b.parent:
                ptail = b.id_data.matrix_world @ b.parent.tail
                v_dir = (b.wiggle.position_head - ptail).normalized()
                target = ptail + v_dir * (b.id_data.matrix_world @ b.head - b.id_data.matrix_world @ b.parent.tail).length
            else:
                target = mat.translation
            s = stretch(target, b.wiggle.position_head, b.wiggle_stretch_head)
            if p and b.wiggle_chain_head:
                if p.wiggle_tail:
                    fac = get_fac(b.wiggle_mass_head, p.wiggle_mass) if i else p.wiggle_stretch
                    tailpos = p.wiggle.matrix @ relative_matrix(p.matrix, b.parent.matrix) @ Vector((0, b.parent.length, 0))
                    denom = (p.wiggle.matrix.translation - tailpos).length
                    ratio = (p.wiggle.matrix.translation - p.wiggle.position).length / denom if denom > 0.0001 else 1.0
                    tailpos -= s * fac
                    p.wiggle.position -= s * ratio * fac
                else:
                    fac = get_fac(b.wiggle_mass_head, p.wiggle_mass_head) if i else p.wiggle_stretch_head
                    p.wiggle.position_head -= s * fac
                b.wiggle.position_head += s * (1 - fac)
            else:
                b.wiggle.position_head += s
                
            target = b.wiggle.position_head + (b.wiggle.position - b.wiggle.position_head).normalized() * length_world(b)
            if b.wiggle_tail:
                s = stretch(target, b.wiggle.position, b.wiggle_stretch)
                if b.wiggle_chain:
                    fac = get_fac(b.wiggle_mass, b.wiggle_mass_head) if i else b.wiggle_stretch_head
                    b.wiggle.position_head -= s * fac
                    b.wiggle.position += s * (1 - fac)
                else:
                    b.wiggle.position += s
            else: b.wiggle.position = target
        else:
            target = mat.translation + (b.wiggle.position - mat.translation).normalized() * length_world(b)
            s = stretch(target, b.wiggle.position, b.wiggle_stretch)
            if p and b.wiggle_chain and p.wiggle_tail:
                fac = get_fac(b.wiggle_mass, p.wiggle_mass)
                if get_pin(b): fac = 1 - b.wiggle_stretch
                if i == 0: fac = p.wiggle_stretch

                if (p == b.parent and b.bone.use_connect):
                    p.wiggle.position -= s * fac
                else:
                    headpos = b.wiggle.matrix.translation
                    v1 = headpos - p.wiggle.matrix.translation
                    headpos -= s * fac
                    v2 = headpos - p.wiggle.matrix.translation
                    v1_len = v1.length
                    if v1_len > 0.001:
                        sc = v2.length / v1_len
                        sc = max(0.99, min(1.01, sc)) # Prevent squashing
                        q = v1.rotation_difference(v2)
                        v3 = q @ (p.wiggle.position - p.wiggle.matrix.translation)
                        p.wiggle.position = p.wiggle.matrix.translation + v3 * sc
                
                b.wiggle.position += s * (1 - fac)
                update_p = True
            else:
                b.wiggle.position += s

        if update_p:
            collide(p, dg)
            update_matrix(p)
            # Blender 5.0 forced normalization
            p.matrix = p.matrix.to_3x3().normalized().to_4x4()
            p.matrix.translation = p.matrix.translation
            
        if b.wiggle_tail:
            pin(b)
            collide(b, dg)
        if b.wiggle_head:
            collide(b, dg, True)
            
    update_matrix(b)
    # Blender 5.0 forced normalization
    b.matrix = b.matrix.to_3x3().normalized().to_4x4()
    b.matrix.translation = b.matrix.translation
# END OF REVISION #


@persistent
def wiggle_post(scene, dg):
    w = scene.wiggle
    cf = scene.frame_current
    
    if w.lastframe == cf and not w.reset: return
    if bpy.context.screen and not bpy.context.screen.is_animation_playing:
        if w.lastframe == cf: return
    if (w.lastframe == cf) and not w.reset and cf > scene.frame_start: return
    if w.reset or not scene.wiggle_enable or w.is_rendering: return

    # [Performance] get_parent()'s result never changes within a frame, so
    # only clear it once per frame - removes the redundant work of
    # constrain() walking back up the parent chain to recompute it every
    # time it's called (iterations times per bone).
    clear_parent_cache()

    lastframe = w.lastframe

    # [Feature fix] The "Loop Physics" toggle used to be duplicated across
    # two properties (scene.wiggle.loop, scene.wiggle_use_loop), and
    # neither was actually read anywhere, so it had no effect. Now unified
    # into scene.wiggle_use_loop and actually wired up: the hard reset is
    # only skipped when the timeline naturally wraps from the end back to
    # the start during playback (a real loop), so the physics state
    # (velocity, etc.) carries over uninterrupted. If the user manually
    # rewinds forward (manual scrubbing), that's not a loop, so it always
    # resets regardless of the toggle.
    loop_enabled = getattr(scene, "wiggle_use_loop", False)
    is_playing = bool(bpy.context.screen and bpy.context.screen.is_animation_playing)
    is_natural_loop = loop_enabled and is_playing and cf <= scene.frame_start and lastframe >= scene.frame_end

    # 2. [Reset logic] - precisely target only the bones in the wiggle list, instead of iterating every bone
    if not is_natural_loop and ((cf <= scene.frame_start) or (cf < lastframe)):
        for wo in w.list:
            ob = scene.objects.get(wo.name)
            if not ob: continue
            if ob.animation_data: ob.update_tag()
            
            for wb in wo.list:
                b = ob.pose.bones.get(wb.name)
                if not b: continue
                bw = b.wiggle
                b.location = (0, 0, 0)
                if b.rotation_mode == 'QUATERNION': b.rotation_quaternion = (1, 0, 0, 0)
                else: b.rotation_euler = (0, 0, 0)
                b.scale = (1, 1, 1)

                m = ob.matrix_world @ b.matrix
                pos = m.to_translation()
                bw.position = pos.copy()
                bw.position_last = pos.copy()
                bw.velocity = Vector((0, 0, 0))
                if hasattr(bw, 'q'): bw.q = Quaternion((1, 0, 0, 0))

            # [Fix] Update only the bones in the list, not every bone, to avoid conflicts
            for wb in wo.list:
                b = ob.pose.bones.get(wb.name)
                if b: update_matrix(b, True)
                
        w.lastframe = cf
        w.reset = False
        return

    # 3. Time calculation (keeps the original values: not multiplied by fe)
    w.dt = (1.0 / max(1.0, scene.render.fps))
    w.lastframe = cf

    # 4. Main loop
    for wo in w.list:
        ob = scene.objects.get(wo.name)
        # [Bug fix] wiggle_freeze gets set to True after baking, but it was
        # never checked here, so physics kept running on top of the baked
        # keyframes and overwriting the result.
        if not ob or ob.wiggle_mute or getattr(ob, "wiggle_freeze", False): continue

        # [Feature added] Disk point cache - if a frame has already been
        # computed, load and apply it directly instead of re-simulating
        # (avoids re-simulating from frame_start every time when scrubbing
        # the timeline).
        cache_enabled = getattr(scene, "wiggle_cache_enable", False)
        if cache_enabled and wiggle_cache.has_frame(scene, ob.name, cf):
            if wiggle_cache.load_frame(scene, ob, wo):
                for wb in wo.list:
                    b = ob.pose.bones.get(wb.name)
                    if b: update_matrix(b, True)
                ob.update_tag()
                continue

        # Safety Boost (kept as a separate per-object calculation)
        safety_boost = 0.0
        if getattr(scene, "wiggle_adaptive_damping", False):
            sensitivity = getattr(scene, "wiggle_safety_threshold", 10.0)

            # 1. Detect movement speed (original)
            if not hasattr(w, "last_ob_pos"): w.last_ob_pos = {}
            current_pos = ob.matrix_world.translation.copy()
            last_pos = w.last_ob_pos.get(ob.name, current_pos)
            delta_move = current_pos - last_pos
            obj_speed = delta_move.length / w.dt if w.dt > 0 else 0
            threshold = 5.0
            if obj_speed > threshold:
                safety_boost = (obj_speed - threshold) * sensitivity
            w.last_ob_pos[ob.name] = current_pos

            # 2. [Added] Detect rotational speed - when a character spins
            # around quickly, position barely changes even though bone tips
            # (tails, hair) can travel a huge actual distance, which
            # position-based detection alone can't catch.
            if not hasattr(w, "last_ob_rot"): w.last_ob_rot = {}
            current_rot = ob.matrix_world.to_quaternion()
            last_rot = w.last_ob_rot.get(ob.name, current_rot)
            angle_delta = last_rot.rotation_difference(current_rot).angle
            angular_speed_deg = math.degrees(angle_delta) / w.dt if w.dt > 0 else 0
            rot_threshold = getattr(scene, "wiggle_safety_rot_threshold", 180.0)
            if angular_speed_deg > rot_threshold:
                rot_boost = (angular_speed_deg - rot_threshold) * sensitivity * 0.1
                safety_boost = max(safety_boost, rot_boost)
            w.last_ob_rot[ob.name] = current_rot

        active_bones = [ob.pose.bones.get(wb.name) for wb in wo.list
                        if ob.pose.bones.get(wb.name) and not ob.pose.bones.get(wb.name).wiggle_mute]
        active_bones = [b for b in active_bones if b is not None]
        if not active_bones: continue

        orig_rots = {b.name: b.rotation_quaternion.copy() if b.rotation_mode == 'QUATERNION'
                     else b.rotation_euler.to_quaternion() for b in active_bones}
        # [Bug fix] The wiggle_influence blend only reverted rotation and
        # never reverted position, so a floating head bone (use_connect
        # off) would stay at wherever physics had moved it even with
        # influence dropped to 0, sticking out like a coil. Now the
        # original animated position is cached too.
        orig_locs = {b.name: b.location.copy() for b in active_bones}
        # Scale caused by Stretch needs to be reverted the same way as
        # rotation/position (otherwise a stretched/squashed shape would
        # remain even at influence=0).
        orig_scales = {b.name: b.scale.copy() for b in active_bones}

        for b in active_bones:
            if hasattr(b.wiggle, "adaptive_damp_mod"):
                b.wiggle.adaptive_damp_mod = safety_boost
            move(b, dg)
            
        for i in range(max(1, w.iterations)):
            for b in active_bones:
                constrain(b, w.iterations - 1 - i, dg, w.dt, w.iterations)

        # [Feature added] Self collision - off by default, opt-in per object.
        # Applied exactly once per frame, not per iteration, to avoid
        # fighting/oscillating with the existing stiffness/stretch constraints.
        if getattr(ob, "wiggle_self_collide", False):
            apply_self_collision(active_bones, getattr(ob, "wiggle_self_collide_margin", 0.0))

        # [Bug fix] Angle Limit (Total Limit) was only applied in move(),
        # and the iterative solver in constrain() that runs afterward knows
        # nothing about angle limits, so it kept dragging the position back
        # out - e.g. setting a 10-degree limit could actually end up much
        # wider (e.g. 90 degrees). Re-clamp once more after all of
        # constrain()'s iterations finish.
        for b in active_bones:
            reclamp_angle_limit(b)

        for b in active_bones:
            update_matrix(b, True)
            inf = getattr(b, "wiggle_influence", 1.0)

            # [Bug fix] The Sim Mix Layers' Layer Weight/Sim Mix never showed
            # up in the final pose at all. (1) It only wrote to
            # b.rotation_quaternion, but if rotation_mode isn't QUATERNION
            # (most game/UE5-style rigs use Euler), Blender doesn't evaluate
            # that channel at all, so it was silently ignored. (2) The
            # subsequent call to update_matrix(b, True) never read
            # rotation_quaternion and recomputed the matrix purely from
            # b.wiggle.position, immediately overwriting the value that was
            # just blended (even when in QUATERNION mode, it had no
            # effect). Fixed by reading the actual simulated rotation from
            # matrix_basis, blending it, and writing to whichever channel
            # matches the bone's actual rotation_mode - this solves both
            # problems. update_matrix() is not called again after this
            # (doing so would ignore the channel just written and overwrite
            # it with the physics state again).
            sim_q = b.matrix_basis.to_quaternion()
            anim_q = orig_rots.get(b.name, Quaternion((1, 0, 0, 0)))

            # [Bug fix] Only rotation was blended while position was left
            # alone. For a bone with a floating wiggle_head (use_connect
            # off), update_matrix() writes b.location directly from the
            # physics position, so even reverting only rotation toward
            # animation left the position sitting at the physics result -
            # the bone would still stick out like a coil even at
            # influence=0. Position is now reverted toward the original
            # animation the same way.
            sim_loc = b.location.copy()
            anim_loc = orig_locs.get(b.name, sim_loc)
            sim_scale = b.scale.copy()
            anim_scale = orig_scales.get(b.name, sim_scale)

            # So the Sim Mix Layers can immediately re-blend with a new
            # influence value without changing the current frame (i.e. just
            # by moving the slider), always cache this frame's pure
            # animation/simulation rotation, position, and scale regardless
            # of the current inf value.
            _LAST_BLEND_CACHE[(ob.name, b.name)] = (
                anim_q.copy(), sim_q.copy(), b.rotation_mode,
                anim_loc.copy(), sim_loc.copy(),
                anim_scale.copy(), sim_scale.copy(),
            )

            if inf < 1.0:
                blended_q = anim_q.slerp(sim_q, inf)
                if b.rotation_mode == 'QUATERNION':
                    b.rotation_quaternion = blended_q
                elif b.rotation_mode == 'AXIS_ANGLE':
                    axis, angle = blended_q.to_axis_angle()
                    b.rotation_axis_angle = (angle, axis.x, axis.y, axis.z)
                else:
                    b.rotation_euler = blended_q.to_euler(b.rotation_mode)
                b.location = anim_loc.lerp(sim_loc, inf)
                b.scale = anim_scale.lerp(sim_scale, inf)

        # 5. Update velocity (keeps the original values: not divided by fe)
        for b in active_bones:
            bw = b.wiggle
            bw.velocity = (bw.position - bw.position_last)
            bw.position_last = bw.position.copy()

        if cache_enabled:
            wiggle_cache.save_frame(scene, ob, wo, cf)

        ob.update_tag()



@persistent
def wiggle_render_pre(scene):
    scene.wiggle.is_rendering = True

@persistent
def wiggle_render_post(scene):
    scene.wiggle.is_rendering = False

@persistent
def wiggle_render_cancel(scene):
    scene.wiggle.is_rendering = False

@persistent
def wiggle_load(dummy):
    if 'build_list' in globals():
        build_list()
    scene = bpy.context.scene
    if scene and hasattr(scene, "wiggle"):
        scene.wiggle.is_rendering = False
        scene.wiggle.lastframe = scene.frame_current
        # Self-heal: scene.wiggle.reset is a plain saved BoolProperty
        # that, if left True (e.g. from a Hard Reset that threw an
        # exception before the WiggleReset fix below), permanently
        # disables all simulation in that file - wiggle_post()'s very
        # first check is "if w.reset: return", with no UI anywhere
        # showing this flag is set. Any file saved in that state would
        # load with simulation silently dead forever. Force it False on
        # every load so an already-broken file heals itself the moment
        # it's reopened, regardless of what left it stuck.
        scene.wiggle.reset = False

# END OF REVISION #

            
class WiggleCopy(bpy.types.Operator):
    """Copy active wiggle settings to selected bones"""
    bl_idname = "wiggle.copy"
    bl_label = "Copy Settings to Selected"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls,context):
        return context.mode in ['POSE'] and context.active_pose_bone and (len(context.selected_pose_bones)>1)

    def execute(self, context):
        b = context.active_pose_bone
        selected_bones = context.selected_pose_bones

        # Instead of a hardcoded property list, automatically iterate over
        # every property with a "wiggle_" prefix and copy it. This way the
        # list never goes stale and misses newly added properties.
        props = [p.identifier for p in b.bl_rna.properties
                  if p.identifier.startswith('wiggle_') and not p.is_readonly]

        for sb in selected_bones:
            if sb == b:
                continue
            for prop in props:
                try:
                    setattr(sb, prop, getattr(b, prop))
                except (AttributeError, TypeError):
                    pass

        return {'FINISHED'}


# START OF REVISION #
class WiggleReset(bpy.types.Operator):
    bl_idname = "wiggle.reset"
    bl_label = "Reset Physics"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls,context):
        return context.scene.wiggle_enable and context.mode in ['OBJECT', 'POSE']

    def execute(self, context):
        # Bug fix: scene.wiggle.reset is a plain saved BoolProperty, and
        # wiggle_post()'s very first check is "if w.reset: return" - it
        # unconditionally disables ALL physics for this scene. This used
        # to set reset=True, then reset=False only at the very end with
        # no try/finally in between. If reset_bones_batch() (or the
        # lookup loop around it) ever threw - e.g. a stale bone
        # reference after deleting a bone/armature, a malformed list
        # entry - the operator aborted partway through and reset stayed
        # True forever. Since it's saved with the file, this could
        # permanently disable simulation on every future load of that
        # file, with no visible indicator anywhere the flag is even
        # exposed in the UI. Wrapped in try/finally so reset is
        # guaranteed to end up False regardless of what happens, and
        # each object's reset is now individually guarded so one bad
        # entry can't abort the rest.
        context.scene.wiggle.reset = True
        try:
            # Gather every bone in the scene and reset them all in one
            # batch (calling view_layer.update() per bone would get very
            # slow on rigs with many bones - reset_bones_batch only
            # updates once for the whole set).
            for wo in context.scene.wiggle.list:
                ob = context.scene.objects.get(wo.name)
                if not ob:
                    continue
                try:
                    bones = [ob.pose.bones.get(wb.name) for wb in wo.list]
                    reset_bones_batch(bones)
                except Exception as e:
                    print(f"Wiggle2: reset failed for '{ob.name}'({e})")

            # Pin the last-computed frame to the current one so the handler doesn't re-track it.
            context.scene.wiggle.lastframe = context.scene.frame_current
        finally:
            context.scene.wiggle.reset = False

        # Force a viewport redraw.
        if context.screen:
            for area in context.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()

        return {'FINISHED'}
# END OF REVISION #

# START OF REVISION #
class WiggleSelect(bpy.types.Operator):
    """Select wiggle bones on selected objects in pose mode"""
    bl_idname = "wiggle.select"
    bl_label = "Select Enabled"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls,context):
        return context.mode in ['POSE']

    def execute(self,context):
        bpy.ops.pose.select_all(action='DESELECT')
        rebuild = False
        first_bone = None
        first_ob = None
        for wo in context.scene.wiggle.list:
            ob = context.scene.objects.get(wo.name)
            if not ob:
                rebuild = True
                continue
            for wb in wo.list:
                b = ob.pose.bones.get(wb.name)
                if not b:
                    rebuild = True
                    continue
                # Depending on the Blender version, neither PoseBone.select
                # nor Bone.select may exist, so handle it defensively
                if hasattr(b, "select"):
                    b.select = True
                if hasattr(b.bone, "select"):
                    b.bone.select = True
                if first_bone is None:
                    first_bone, first_ob = b, ob

        # [Bug fix] This only newly selected bones and never changed the
        # active bone, so if the active bone happened to be unrelated to
        # wiggle before clicking (e.g. one with Tail disabled), that state
        # just stayed. The properties panel would then keep showing the
        # old active bone's values instead of the just-selected bones', and
        # toggling a checkbox there would have update_prop() propagate that
        # value across the (actually) selected bones, incorrectly
        # enabling/disabling unrelated bones too. Now one of the
        # just-selected bones is set as the active bone so the panel always
        # shows the values of an actually selected bone.
        if first_bone is not None:
            first_ob.data.bones.active = first_bone.bone

        if rebuild: build_list()
        return {'FINISHED'}
# END OF REVISION #

    
class WiggleBake(bpy.types.Operator):
    """Bake this object's visible wiggle bones to keyframes with Seamless Loop"""
    bl_idname = "wiggle.bake"
    bl_label = "Bake Wiggle"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == 'ARMATURE'

    def execute(self, context):
        # 1. Initial setup
        obj = context.active_object
        scene = context.scene
        wiggle_props = scene.wiggle
        
        start_frame = scene.frame_start
        end_frame = scene.frame_end
        original_frame = scene.frame_current
        duration = end_frame - start_frame
        # Loop-correction window (last 20%)
        blend_frames = int(duration * 0.2) 

        bake_bones = [
            b for b in obj.pose.bones 
            if (getattr(b, "wiggle_head", False) or getattr(b, "wiggle_tail", False)) 
            and not getattr(b, "wiggle_mute", False)
        ]

        if not bake_bones:
            self.report({'WARNING'}, "No bones to bake.")
            return {'CANCELLED'}

        if obj.mode != 'POSE':
            bpy.ops.object.mode_set(mode='POSE')

        if not obj.animation_data:
            obj.animation_data_create()

        # [Bug fix] When "Overwrite Current Action" was enabled, this used
        # to simply call keyframe_insert on the currently active action.
        # That was "overlaying," not "overwriting": (1) the existing
        # keyframes were never cleared, so new keys got mixed in on top of
        # them, and (2) if that action was also already linked into an
        # unmuted NLA strip and being blended, Blender would evaluate "this
        # action in the NLA stack" plus "the same action as the active
        # action" simultaneously, applying the pose twice on top of itself
        # (matches exactly the "wiggle result doubles up" symptom users
        # reported). For a true overwrite: the existing keyframes must be
        # cleared first, and the track has to be muted during baking so the
        # action isn't evaluated simultaneously with its own NLA strip.
        from . import wiggle_layers as _wl
        target_track = None
        target_track_prev_mute = None
        if not wiggle_props.bake_overwrite:
            new_action = bpy.data.actions.new(name="WiggleAction")
        else:
            action = obj.animation_data.action
            if not action:
                new_action = bpy.data.actions.new(name="WiggleAction")
            else:
                _wl._clear_action_keyframes(action)
                new_action = action

        # [Bug fix, key one] This used to set the action to be baked as the
        # active action right before the loop. Regardless of NLA track mute
        # state, Blender always evaluates the "active action" once more on
        # top of the NLA stack with REPLACE and influence=1.0 (a separate
        # evaluation path). Since that action starts out empty, frame 1 is
        # fine, but the moment the first keyframe is inserted, the action
        # itself, acting as the "active action," starts overwriting the
        # same channels again - so from frame 2 onward, what got baked
        # wasn't the true Base+Sim (NLA) blend but a read of "the just-baked
        # previous frame's value" reapplied (matches exactly the "result is
        # just the physics, not the Base+Sim combination" symptom users
        # reported). The fix: during the "read" phase, without touching the
        # active action yet (so there's no overlay concern), capture each
        # frame's purely-evaluated pose values into Python, then only in
        # the subsequent "write" phase set the active action and write the
        # captured values directly as keyframes per frame (since there's no
        # re-evaluation, the overlay problem never arises).
        _wl._baking_in_progress = True
        captured = []  # list of (frame, {bone_name: (loc, rot_mode, rot, scale)})
        try:
            # 2. Preroll stabilization
            scene.wiggle.is_preroll = True
            preroll_start = start_frame - max(1, wiggle_props.preroll)
            for f in range(preroll_start, start_frame):
                scene.frame_set(f)
                context.view_layer.update()
            scene.wiggle.is_preroll = False

            # 3. Main capture loop (the active action is not touched yet)
            for f in range(start_frame, end_frame + 1):
                scene.frame_set(f)
                context.view_layer.update()
                frame_data = {}
                for pbone in bake_bones:
                    if pbone.rotation_mode == 'QUATERNION':
                        rot = pbone.rotation_quaternion.copy()
                    elif pbone.rotation_mode == 'AXIS_ANGLE':
                        rot = pbone.rotation_axis_angle[:]
                    else:
                        rot = pbone.rotation_euler.copy()
                    frame_data[pbone.name] = (pbone.location.copy(), pbone.rotation_mode, rot, pbone.scale.copy())
                captured.append((f, frame_data))

            # 4. Now set the active action and write the captured values directly.
            obj.animation_data.action = new_action
            if wiggle_props.bake_overwrite:
                target_track = _wl._find_track_for_action(obj, new_action)
                if target_track:
                    target_track_prev_mute = target_track.mute
                    target_track.mute = True
            if hasattr(obj.animation_data, "action_influence"):
                obj.animation_data.action_influence = 1.0
            if hasattr(obj.animation_data, "action_blend_type"):
                obj.animation_data.action_blend_type = 'REPLACE'

            for f, frame_data in captured:
                for pbone in bake_bones:
                    loc, rot_mode, rot, scale = frame_data[pbone.name]
                    pbone.location = loc
                    pbone.keyframe_insert(data_path="location", frame=f, group=pbone.name)
                    if rot_mode == 'QUATERNION':
                        pbone.rotation_quaternion = rot
                        pbone.keyframe_insert(data_path="rotation_quaternion", frame=f, group=pbone.name)
                    elif rot_mode == 'AXIS_ANGLE':
                        pbone.rotation_axis_angle = rot
                        pbone.keyframe_insert(data_path="rotation_axis_angle", frame=f, group=pbone.name)
                    else:
                        pbone.rotation_euler = rot
                        pbone.keyframe_insert(data_path="rotation_euler", frame=f, group=pbone.name)
                    pbone.scale = scale
                    pbone.keyframe_insert(data_path="scale", frame=f, group=pbone.name)
        finally:
            _wl._baking_in_progress = False
            if target_track:
                target_track.mute = target_track_prev_mute if target_track_prev_mute is not None else False

        # 4. [Key step] Seamless Loop correction (includes AttributeError fix)
        if obj.animation_data and obj.animation_data.action:
            action = obj.animation_data.action
            bone_names = {b.name for b in bake_bones}

            # Handle Blender 4.0+ .curves / older-version .fcurves compatibility
            curves = getattr(action, "curves", getattr(action, "fcurves", []))

            for fc in curves:
                if any(f'["{name}"]' in fc.data_path for name in bone_names):
                    # Capture the start-frame value
                    start_val = fc.evaluate(start_frame)

                    # Run the loop blend
                    if blend_frames > 0:
                        for f in range(end_frame - blend_frames, end_frame + 1):
                            t = (f - (end_frame - blend_frames)) / blend_frames
                            current_val = fc.evaluate(f)
                            # Gradually interpolate the end value toward the start value
                            blended_val = current_val * (1.0 - t) + start_val * t
                            fc.keyframe_points.insert(f, blended_val, options={'FAST'})

                    # Clean up handles
                    for kp in fc.keyframe_points:
                        kp.interpolation = 'BEZIER'
                        kp.handle_left_type = 'AUTO'
                        kp.handle_right_type = 'AUTO'
                    fc.update()

        # 5. Wrap-up and restore
        obj.wiggle_freeze = True
        scene.frame_set(original_frame)
        
        self.report({'INFO'}, f"Bake Complete with Seamless Loop: {start_frame} ~ {end_frame}")
        return {'FINISHED'}


class WiggleBakeCache(bpy.types.Operator):
    """Simulate the full scene frame range once and save every frame to disk,
    so later scrubbing can load instead of re-simulating from frame_start"""
    bl_idname = "wiggle.bake_cache"
    bl_label = "Bake to Disk Cache"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene.wiggle_enable

    def execute(self, context):
        scene = context.scene
        start, end = scene.frame_start, scene.frame_end
        original_frame = scene.frame_current
        was_enabled = getattr(scene, "wiggle_cache_enable", False)

        wiggle_cache.clear_cache(scene)
        scene.wiggle_cache_enable = True
        try:
            for f in range(start, end + 1):
                scene.frame_set(f)
        finally:
            scene.wiggle_cache_enable = was_enabled
            scene.frame_set(original_frame)

        self.report({'INFO'}, f"Cached frames {start}-{end}")
        return {'FINISHED'}


class WiggleClearCache(bpy.types.Operator):
    """Delete all cached Wiggle 2 frame files on disk"""
    bl_idname = "wiggle.clear_cache"
    bl_label = "Clear Disk Cache"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        n = wiggle_cache.clear_cache(context.scene)
        self.report({'INFO'}, f"Removed {n} cached frame file(s)")
        return {'FINISHED'}


# START OF REVISION #

class WiggleToggleBBox(bpy.types.Operator):
    bl_idname = "wiggle.toggle_bbox"
    bl_label = "Add/Remove Visual Guide"
    bl_options = {'REGISTER', 'UNDO'}
    bl_description = "Select a mesh to use as a visual guide"
    @staticmethod
    def update_mesh_shape(pb, context):
        name = f"WGuide_{pb.name}"
        g = bpy.data.objects.get(name)
        if not g or g.type != 'MESH': return
        
        length = pb.bone.length
        t = pb.wiggle_radius * 2 if pb.wiggle_radius > 0.0005 else 0.001
        offset = length / t
        
        is_capsule = len(g.data.vertices) > 100
        for v in g.data.vertices:
            if is_capsule:
                # On creation, split the initial vertices into Head (0) and Tail (offset)
                if v.co.z > 0.01:
                    v.co.z = (v.co.z - 0.5) + offset
                else:
                    v.co.z = v.co.z + 0.5
            else: # BOX, CYLINDER
                v.co.z = (v.co.z + 0.5) * offset
        
        g["last_offset"] = offset
        g.scale = (t, t, t)

    def execute(self, context):
        arm = context.object
        scene = context.scene
        selected_bones = getattr(context, "selected_pose_bones", []) or []
        if not selected_bones: return {'CANCELLED'}

        first_name = f"WGuide_{selected_bones[0].name}"
        exists = bpy.data.objects.get(first_name) is not None
        original_mode = context.mode

        if bpy.ops.object.mode_set.poll():
            bpy.ops.object.mode_set(mode='OBJECT')

        if exists:
            for pb in selected_bones:
                obj = bpy.data.objects.get(f"WGuide_{pb.name}")
                if obj: bpy.data.objects.remove(obj, do_unlink=True)
        else:
            shape = getattr(scene, "wiggle_guide_shape", 'CAPSULE')
            for pb in selected_bones:
                if shape == 'BOX': 
                    bpy.ops.mesh.primitive_cube_add(size=1.0)
                elif shape == 'CYLINDER': 
                    bpy.ops.mesh.primitive_cylinder_add(vertices=16, radius=0.5, depth=1.0)
                else: # CAPSULE
                    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5, segments=16, ring_count=8)

                g = context.active_object
                g.name = f"WGuide_{pb.name}"
                g.display_type, g.hide_render = 'WIRE', True
                
                # Parent to the bone and align its transform
                g.parent, g.parent_type, g.parent_bone = arm, 'BONE', pb.name
                g.matrix_local = Matrix.Identity(4)
                g.rotation_euler = (1.570796, 0, 0) # Align to the bone's direction of travel

                # Call the initial shape-correction function
                WiggleToggleBBox.update_mesh_shape(pb, context)

        # Reset the active object so we can return to pose mode
        context.view_layer.objects.active = arm
        if bpy.ops.object.mode_set.poll():
            try:
                bpy.ops.object.mode_set(mode=original_mode)
            except:
                bpy.ops.object.mode_set(mode='OBJECT')
            
        return {'FINISHED'}
    
# END OF REVISION #
#N START OF REVISION #
class WigglePreset(bpy.types.Operator):
    bl_idname = "wiggle.preset"
    bl_label = "Physics Preset"
    bl_options = {'REGISTER', 'UNDO'}
    
    type: bpy.props.StringProperty()

    def execute(self, context):
        # [Critical bug fix] Use selected_bones (all bones multi-selected via drag) instead of just the single active_pose_bone.
        selected_bones = getattr(context, "selected_pose_bones", []) or []
        if not selected_bones:
            self.report({'WARNING'}, "No pose bone selected")
            return {'CANCELLED'}
            
        scene = context.scene
        
        try:
            from . import physics_logic as _pl
            apply_func = _pl.apply_taper_to_chain
        except Exception as e:
            self.report({'ERROR'}, f"Module Load Error: {e}")
            return {'CANCELLED'}
            
        # Look up the matching values for each preset type
        if self.type == 'JELLY':
            s_start, s_end = 20.0, 5.0
            d_start, d_end = 1.0, 0.1
            gravity, friction, bounce = 0.5, 0.4, 0.8
        elif self.type == 'HAIR':
            s_start, s_end = 50.0, 0.0
            d_start, d_end = 5.0, 0.0
            gravity, friction, bounce = 1.0, 0.8, 0.1
        elif self.type == 'HEAVY':
            s_start, s_end = 40.0, 10.0
            d_start, d_end = 4.0, 0.5
            gravity, friction, bounce = 2.5, 0.9, 0.1
        elif self.type == 'CLOTH':
            s_start, s_end = 15.0, 2.0
            d_start, d_end = 3.0, 0.5
            gravity, friction, bounce = 0.8, 0.7, 0.1
        elif self.type == 'SPRING':
            s_start, s_end = 60.0, 30.0
            d_start, d_end = 0.5, 0.1
            gravity, friction, bounce = 0.3, 0.5, 0.9
        elif self.type == 'ANTENNA':
            s_start, s_end = 50.0, 20.0
            d_start, d_end = 0.8, 0.2
            gravity, friction, bounce = 0.1, 0.5, 0.5
        else:
            return {'CANCELLED'}
            
        # [Precision fix] Loop over every drag-selected bone and force-apply this physics setting to all of them.
        for b in selected_bones:
            b.wiggle_stiff_use_dist = True
            b.wiggle_damp_use_dist = True
            b.wiggle_gravity = gravity
            b.wiggle_friction = friction
            b.wiggle_bounce = bounce
            
        scene.wiggle_stiff_start = s_start
        scene.wiggle_stiff_end = s_end
        scene.wiggle_damp_start = d_start
        scene.wiggle_damp_end = d_end
        
        apply_func(context, "wiggle_stiff", s_start, s_end)
        apply_func(context, "wiggle_damp", d_start, d_end)
        
        if bpy.ops.wiggle.reset.poll():
            bpy.ops.wiggle.reset()
            
        return {'FINISHED'}



class WiggleBoneItem(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(override={'LIBRARY_OVERRIDABLE'})

class WiggleItem(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(override={'LIBRARY_OVERRIDABLE'})
    list: bpy.props.CollectionProperty(type=WiggleBoneItem, override={'LIBRARY_OVERRIDABLE','USE_INSERTION'})

#store properties for a bone. custom properties for user editable. property group for internal calculations
class WiggleBone(bpy.types.PropertyGroup):
    matrix: bpy.props.FloatVectorProperty(name = 'Matrix', size=16, subtype = 'MATRIX', override={'LIBRARY_OVERRIDABLE'})
    position: bpy.props.FloatVectorProperty(subtype='TRANSLATION', override={'LIBRARY_OVERRIDABLE'})
    position_last: bpy.props.FloatVectorProperty(subtype='TRANSLATION', override={'LIBRARY_OVERRIDABLE'})
    velocity: bpy.props.FloatVectorProperty(subtype='VELOCITY', override={'LIBRARY_OVERRIDABLE'})
    
    collision_point:bpy.props.FloatVectorProperty(subtype = 'TRANSLATION', override={'LIBRARY_OVERRIDABLE'})
    collision_ob: bpy.props.PointerProperty(type=bpy.types.Object, override={'LIBRARY_OVERRIDABLE'})
    collision_normal: bpy.props.FloatVectorProperty(subtype = 'TRANSLATION', override={'LIBRARY_OVERRIDABLE'})
    collision_col: bpy.props.PointerProperty(type=bpy.types.Collection,override={'LIBRARY_OVERRIDABLE'})
    
    position_head: bpy.props.FloatVectorProperty(subtype='TRANSLATION', override={'LIBRARY_OVERRIDABLE'})
    position_last_head: bpy.props.FloatVectorProperty(subtype='TRANSLATION', override={'LIBRARY_OVERRIDABLE'})
    velocity_head: bpy.props.FloatVectorProperty(subtype='VELOCITY', override={'LIBRARY_OVERRIDABLE'})
    
    collision_point_head:bpy.props.FloatVectorProperty(subtype = 'TRANSLATION', override={'LIBRARY_OVERRIDABLE'})
    collision_ob_head: bpy.props.PointerProperty(type=bpy.types.Object, override={'LIBRARY_OVERRIDABLE'})
    collision_normal_head: bpy.props.FloatVectorProperty(subtype = 'TRANSLATION', override={'LIBRARY_OVERRIDABLE'})
    # Internal computed value for Adaptive Safety Guard
    adaptive_damp_mod: bpy.props.FloatProperty(default=0.0, override={'LIBRARY_OVERRIDABLE'})

class WiggleObject(bpy.types.PropertyGroup):
    list: bpy.props.CollectionProperty(type=WiggleItem, override={'LIBRARY_OVERRIDABLE'})
    
class WiggleScene(bpy.types.PropertyGroup):
    dt: bpy.props.FloatProperty()
    lastframe: bpy.props.IntProperty()
    iterations: bpy.props.IntProperty(name='Quality', description='Constraint solver interations for chain physics', min=1, default=2, soft_max=10, max=100)
    loop: bpy.props.BoolProperty(name='Loop Physics', description='Physics continues as timeline loops', default=True)
    list: bpy.props.CollectionProperty(type=WiggleItem, override={'LIBRARY_OVERRIDABLE','USE_INSERTION'})
    preroll: bpy.props.IntProperty(name = 'Preroll', description='Frames to run simulation before bake', min=0, default=0)
    is_preroll: bpy.props.BoolProperty(default=False)
    bake_overwrite: bpy.props.BoolProperty(name='Overwrite Current Action', description='Bake wiggle into current action, instead of creating a new one', default = False)
    bake_nla: bpy.props.BoolProperty(name='Current Action to NLA', description='Move existing animation on the armature into an NLA strip', default = False) 
    is_rendering: bpy.props.BoolProperty(default=False)
    reset: bpy.props.BoolProperty(default=False)

def register():
    bpy.types.Scene.wiggle_enable = bpy.props.BoolProperty(
        name = 'Enable Scene',
        default = False,
        override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_enable')
    )
    bpy.types.Object.wiggle_enable = bpy.props.BoolProperty(
        name = 'Enable Armature',
        default = False,
        options={'HIDDEN'},
        override={'LIBRARY_OVERRIDABLE'}
    )
    bpy.types.Object.wiggle_mute = bpy.props.BoolProperty(
        name = 'Mute Armature',
        default = False,
        override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_mute')
    )
    bpy.types.Object.wiggle_freeze = bpy.props.BoolProperty(
        name = 'Freeze Wiggle',
        default = False,
        override={'LIBRARY_OVERRIDABLE'}
    )
    bpy.types.Object.wiggle_self_collide = bpy.props.BoolProperty(
        name='Self Collision', default=False,
        description="Wiggle tail/head points push apart from each other (point-based, not full capsule) - off by default"
    )
    bpy.types.Object.wiggle_self_collide_margin = bpy.props.FloatProperty(
        name='Self Collision Margin', default=0.0, min=0.0, soft_max=0.1
    )
    bpy.types.PoseBone.wiggle_enable = bpy.props.BoolProperty(
        name = 'Enable Bone',
        default = False,
        options={'HIDDEN'},
        override={'LIBRARY_OVERRIDABLE'}
    )
    bpy.types.PoseBone.wiggle_mute = bpy.props.BoolProperty(
        name = 'Mute Bone',
        default = False,
        override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_mute')
    )
    bpy.types.PoseBone.wiggle_head = bpy.props.BoolProperty(
        name = 'Bone Head',
        default = False,
        override={'LIBRARY_OVERRIDABLE'},
        options={'HIDDEN'},
        update=lambda s, c: update_prop(s, c, 'wiggle_head')
    )
    bpy.types.PoseBone.wiggle_tail = bpy.props.BoolProperty(
        name = 'Bone Tail',
        default = False,
        override={'LIBRARY_OVERRIDABLE'},
        options={'HIDDEN'},
        update=lambda s, c: update_prop(s, c, 'wiggle_tail')
    )
    bpy.types.PoseBone.wiggle_head_mute = bpy.props.BoolProperty(
        name = 'Bone Head Mute',
        default = False,
        override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_head_mute')
    )
    bpy.types.PoseBone.wiggle_tail_mute = bpy.props.BoolProperty(
        name = 'Bone Tail Mute',
        default = False,
        override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_tail_mute')
    )
    bpy.types.PoseBone.wiggle_mass = bpy.props.FloatProperty(
        name = 'Mass', min = 0.01, default = 1, override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_mass')
    )
    bpy.types.PoseBone.wiggle_stiff = bpy.props.FloatProperty(
        name = 'Stiff', min = 0, default = 20, override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_stiff')
    )
    bpy.types.PoseBone.wiggle_stretch = bpy.props.FloatProperty(
        name = 'Stretch', min = 0, default = 0, max=1, override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_stretch')
    )
    bpy.types.PoseBone.wiggle_damp = bpy.props.FloatProperty(
        name = 'Damp', min = 0, default = 1, override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_damp')
    )
    # wiggle_angle_limit is registered in ui_panel.py (final definition,
    # including precision, etc.). Registering it again here would let
    # registration order overwrite one with the other and tangle up
    # unregister(), so it's removed from here.

    bpy.types.PoseBone.wiggle_gravity = bpy.props.FloatProperty(
        name = 'Gravity', default = 1, override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_gravity')
    )
    bpy.types.PoseBone.wiggle_wind_ob = bpy.props.PointerProperty(
        name='Wind', type=bpy.types.Object, poll = wind_poll, override={'LIBRARY_OVERRIDABLE'}, 
        update=lambda s, c: update_prop(s, c, 'wiggle_wind_ob')
    )
    bpy.types.PoseBone.wiggle_wind = bpy.props.FloatProperty(
        name = 'Wind Multiplier', default = 1, override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_wind')
    )
    bpy.types.PoseBone.wiggle_chain = bpy.props.BoolProperty(
        name = 'Chain', default = True, override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_chain')
    )
    bpy.types.PoseBone.wiggle_mass_head = bpy.props.FloatProperty(
        name = 'Mass', min = 0.01, default = 1, override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_mass_head')
    )
    bpy.types.PoseBone.wiggle_stiff_head = bpy.props.FloatProperty(
        name = 'Stiff', min = 0, default = 50, override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_stiff_head')
    )
    bpy.types.PoseBone.wiggle_stretch_head = bpy.props.FloatProperty(
        name = 'Stretch', min = 0, default = 0, max=1, override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_stretch_head')
    )
    bpy.types.PoseBone.wiggle_damp_head = bpy.props.FloatProperty(
        name = 'Damp', min = 0, default = 1, override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_damp_head')
    )
    bpy.types.PoseBone.wiggle_gravity_head = bpy.props.FloatProperty(
        name = 'Gravity', default = 1, override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_gravity_head')
    )
    bpy.types.PoseBone.wiggle_wind_ob_head = bpy.props.PointerProperty(
        name='Wind', type=bpy.types.Object, poll = wind_poll, override={'LIBRARY_OVERRIDABLE'}, 
        update=lambda s, c: update_prop(s, c, 'wiggle_wind_ob_head')
    )
    bpy.types.PoseBone.wiggle_wind_head = bpy.props.FloatProperty(
        name = 'Wind', default = 1, override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_wind_head')
    )
    bpy.types.PoseBone.wiggle_chain_head = bpy.props.BoolProperty(
        name = 'Chain', default = True, override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_chain_head')
    )
    bpy.types.PoseBone.wiggle_collider_type = bpy.props.EnumProperty(
        name='Collider Type', items=[
            ('Object', "Mesh Object", "Collide against any mesh (uses actual surface)"),
            ('Collection', "Collection", "Collide against every mesh in a collection"),
            ('Sphere', "Sphere", "Analytic sphere - no mesh needed, radius 1 scaled by object scale"),
            ('Box', "Box", "Analytic box - no mesh needed, half-extent 1 scaled by object scale"),
            ('Cylinder', "Cylinder", "Analytic cylinder along local Z - no mesh needed, radius/half-height 1 scaled by object scale"),
            ('Capsule', "Capsule", "Analytic capsule along local Z - no mesh needed, radius/half-height 1 scaled by object scale"),
        ],
        override={'LIBRARY_OVERRIDABLE'}, update=lambda s, c: update_prop(s, c, 'wiggle_collider_type')
    )
    bpy.types.PoseBone.wiggle_collider = bpy.props.PointerProperty(
        name='Collider Object', type=bpy.types.Object, poll = collider_poll, override={'LIBRARY_OVERRIDABLE'}, 
        update=lambda s, c: update_prop(s, c, 'wiggle_collider')
    )
    bpy.types.PoseBone.wiggle_collider_collection = bpy.props.PointerProperty(
        name = 'Collider Collection', type=bpy.types.Collection, override={'LIBRARY_OVERRIDABLE'}, 
        update=lambda s, c: update_prop(s, c, 'wiggle_collider_collection')
    )
    bpy.types.PoseBone.wiggle_radius = bpy.props.FloatProperty(
        name = 'Radius', min = 0, default = 0.05, override={'LIBRARY_OVERRIDABLE'},
        update=update_visual_guide
    )
    bpy.types.PoseBone.wiggle_friction = bpy.props.FloatProperty(
        name = 'Friction', min = 0, default = 0.5, soft_max = 1, override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_friction')
    )
    bpy.types.PoseBone.wiggle_bounce = bpy.props.FloatProperty(
        name = 'Bounce', min = 0, default = 0.5, soft_max = 1, override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_bounce')
    )
    bpy.types.PoseBone.wiggle_sticky = bpy.props.FloatProperty(
        name = 'Sticky', min = 0, default = 0, soft_max = 1, override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_sticky')
    )
    bpy.types.PoseBone.wiggle_collider_type_head = bpy.props.EnumProperty(
        name='Collider Type', items=[
            ('Object', "Mesh Object", "Collide against any mesh (uses actual surface)"),
            ('Collection', "Collection", "Collide against every mesh in a collection"),
            ('Sphere', "Sphere", "Analytic sphere - no mesh needed, radius 1 scaled by object scale"),
            ('Box', "Box", "Analytic box - no mesh needed, half-extent 1 scaled by object scale"),
            ('Cylinder', "Cylinder", "Analytic cylinder along local Z - no mesh needed, radius/half-height 1 scaled by object scale"),
            ('Capsule', "Capsule", "Analytic capsule along local Z - no mesh needed, radius/half-height 1 scaled by object scale"),
        ],
        override={'LIBRARY_OVERRIDABLE'}, update=lambda s, c: update_prop(s, c, 'wiggle_collider_type_head')
    )
    bpy.types.PoseBone.wiggle_collider_head = bpy.props.PointerProperty(
        name='Collider Object', type=bpy.types.Object, poll = collider_poll, override={'LIBRARY_OVERRIDABLE'}, 
        update=lambda s, c: update_prop(s, c, 'wiggle_collider_head')
    )
    bpy.types.PoseBone.wiggle_collider_collection_head = bpy.props.PointerProperty(
        name = 'Collider Collection', type=bpy.types.Collection, override={'LIBRARY_OVERRIDABLE'}, 
        update=lambda s, c: update_prop(s, c, 'wiggle_collider_collection_head')
    )
    bpy.types.PoseBone.wiggle_radius_head = bpy.props.FloatProperty(
        name = 'Radius', min = 0, default = 0, override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_radius_head')
    )
    bpy.types.PoseBone.wiggle_friction_head = bpy.props.FloatProperty(
        name = 'Friction', min = 0, default = 0.5, soft_max = 1, override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_friction_head')
    )
    bpy.types.PoseBone.wiggle_bounce_head = bpy.props.FloatProperty(
        name = 'Bounce', min = 0, default = 0.5, soft_max = 1, override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_bounce_head')
    )
    bpy.types.PoseBone.wiggle_sticky_head = bpy.props.FloatProperty(
        name = 'Sticky', min = 0, default = 0, soft_max = 1, override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_sticky_head')
    )
    
    classes = (
        WiggleToggleBBox, WigglePreset, WiggleBoneItem, WiggleItem, WiggleBone,
        WiggleObject, WiggleScene, WiggleReset, WiggleCopy, WiggleSelect, WiggleBake,
        WiggleBakeCache, WiggleClearCache
    )
    # [Bug fix] hasattr(bpy.types, cls.__name__) doesn't reflect whether an
    # Operator/Panel class is actually registered at all (confirmed live on
    # Blender 5.x - register_class/bpy.ops work fine, but hasattr stays
    # False both before and after registration). So this guard could never
    # catch "already registered," and re-registering (Reload Scripts,
    # disabling and re-enabling, etc.) raised an "already registered"
    # exception. Need to judge based on the actual exception via
    # try/except instead.
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            pass

    bpy.types.PoseBone.wiggle = bpy.props.PointerProperty(type=WiggleBone, override={'LIBRARY_OVERRIDABLE'})
    bpy.types.Object.wiggle = bpy.props.PointerProperty(type=WiggleObject, override={'LIBRARY_OVERRIDABLE'})
    bpy.types.Scene.wiggle = bpy.props.PointerProperty(type=WiggleScene, override={'LIBRARY_OVERRIDABLE'})

    # Point cache (disk)
    if not hasattr(bpy.types.Scene, "wiggle_cache_enable"):
        bpy.types.Scene.wiggle_cache_enable = bpy.props.BoolProperty(
            name="Use Disk Cache",
            description="Load cached frames instead of re-simulating when available",
            default=False
        )
    if not hasattr(bpy.types.Scene, "wiggle_cache_dir"):
        bpy.types.Scene.wiggle_cache_dir = bpy.props.StringProperty(
            name="Cache Directory", subtype='DIR_PATH', default="//wiggle2_cache/"
        )

    h_post = bpy.app.handlers.frame_change_post
    if wiggle_post not in h_post: h_post.append(wiggle_post)
    h_r_pre = bpy.app.handlers.render_pre
    if wiggle_render_pre not in h_r_pre: h_r_pre.append(wiggle_render_pre)
    h_r_post = bpy.app.handlers.render_post
    if wiggle_render_post not in h_r_post: h_r_post.append(wiggle_render_post)
    h_r_can = bpy.app.handlers.render_cancel
    if wiggle_render_cancel not in h_r_can: h_r_can.append(wiggle_render_cancel)
    h_load = bpy.app.handlers.load_post
    if wiggle_load not in h_load: h_load.append(wiggle_load)
    # wiggle_influence is registered in wiggle_layers.py (default=1.0).
    # Registering it again here would overwrite that default value, so it's removed from here.
    # wiggle_use_lattice / wiggle_lattice_stiffness / wiggle_lattice_show_debug
    # are registered together with the wiggle_lattice_visual module in __init__.py.

def unregister():
    # [Bug fix] While PointerProperty (bpy.types.*.wiggle, etc.) still
    # references the WiggleBone/WiggleObject/WiggleScene PropertyGroup
    # classes, calling unregister_class on those classes first raises an
    # exception, which stops the reversed(classes) loop partway through -
    # classes after that point (WiggleToggleBBox, etc.) never get
    # unregistered. Then disabling and re-enabling the addon (re-register)
    # leaves an "already registered as a subclass" error. So the
    # properties (especially PointerProperty) must be cleared before the
    # classes are unregistered.
    for attr in ("wiggle_cache_enable", "wiggle_cache_dir", "wiggle_enable", "wiggle"):
        if hasattr(bpy.types.Scene, attr):
            try: delattr(bpy.types.Scene, attr)
            except Exception: pass

    for attr in ("wiggle_self_collide", "wiggle_self_collide_margin",
                 "wiggle_enable", "wiggle_mute", "wiggle_freeze", "wiggle"):
        if hasattr(bpy.types.Object, attr):
            try: delattr(bpy.types.Object, attr)
            except Exception: pass

    for attr in (
        "wiggle_enable", "wiggle_mute", "wiggle_head", "wiggle_tail",
        "wiggle_head_mute", "wiggle_tail_mute", "wiggle_mass", "wiggle_stiff",
        "wiggle_stretch", "wiggle_damp", "wiggle_gravity", "wiggle_wind_ob",
        "wiggle_wind", "wiggle_chain", "wiggle_mass_head", "wiggle_stiff_head",
        "wiggle_stretch_head", "wiggle_damp_head", "wiggle_gravity_head",
        "wiggle_wind_ob_head", "wiggle_wind_head", "wiggle_chain_head",
        "wiggle_collider_type", "wiggle_collider", "wiggle_collider_collection",
        "wiggle_radius", "wiggle_friction", "wiggle_bounce", "wiggle_sticky",
        "wiggle_collider_type_head", "wiggle_collider_head",
        "wiggle_collider_collection_head", "wiggle_radius_head",
        "wiggle_friction_head", "wiggle_bounce_head", "wiggle_sticky_head",
        "wiggle",
    ):
        if hasattr(bpy.types.PoseBone, attr):
            try: delattr(bpy.types.PoseBone, attr)
            except Exception: pass

    classes = (
        WiggleToggleBBox, WigglePreset, WiggleBoneItem, WiggleItem, WiggleBone,
        WiggleObject, WiggleScene, WiggleReset, WiggleCopy, WiggleSelect, WiggleBake,
        WiggleBakeCache, WiggleClearCache
    )
    # [Bug fix] For the same reason as register(), remove the hasattr guard
    # and switch to try/except - since hasattr(bpy.types, cls.__name__) is
    # always False, this guard meant unregister_class was effectively never
    # being called.
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except (RuntimeError, ValueError):
            pass

    if wiggle_post in bpy.app.handlers.frame_change_post: bpy.app.handlers.frame_change_post.remove(wiggle_post)
    if wiggle_render_pre in bpy.app.handlers.render_pre: bpy.app.handlers.render_pre.remove(wiggle_render_pre)
    if wiggle_render_post in bpy.app.handlers.render_post: bpy.app.handlers.render_post.remove(wiggle_render_post)
    if wiggle_render_cancel in bpy.app.handlers.render_cancel: bpy.app.handlers.render_cancel.remove(wiggle_render_cancel)
    if wiggle_load in bpy.app.handlers.load_post: bpy.app.handlers.load_post.remove(wiggle_load)
