import bpy
import mathutils
import gpu
import math
from gpu_extras.batch import batch_for_shader

_WIGGLE_RTX_GL_ENGINE = None

class WiggleRTXHorizontalEngine:
    def __init__(self, armature_obj):
        self.obj = armature_obj
        self.tail_pairs = []
        self.stiffness = 0.02
        self.stretch_elasticity = 0.01
        self.damping_to_rest = 0.5
        self.prev_force_dirs = {}
        self.mesh_obj = None
        self.build_name_based_network()
        self.create_lattice_mesh()

    def build_name_based_network(self):
        try:
            pose_bones = self.obj.pose.bones
        except (ReferenceError, AttributeError):
            return
        if not pose_bones:
            return

        # Bug fix: only target bones with wiggle_tail on, not every bone.
        # The original code paired up completely unrelated bones at the
        # same depth (e.g. arms/ribs) and rotated them in
        # evaluate_physics().
        wiggle_bones = [pb for pb in pose_bones if getattr(pb, "wiggle_tail", False)]
        if len(wiggle_bones) < 3:
            return

        matrix_map = {}
        for pb in wiggle_bones:
            depth = 0
            parent = pb.parent
            while parent:
                depth += 1
                parent = parent.parent
            if depth not in matrix_map:
                matrix_map[depth] = []
            matrix_map[depth].append(pb)

        for depth, bones in matrix_map.items():
            if len(bones) < 3:
                continue

            # Sort bones by world-space X (default baseline).
            sorted_bones_base = sorted(bones, key=lambda pb: (self.obj.matrix_world @ pb.head).x)
            num_bones = len(sorted_bones_base)
            is_ring = False

            # Bug fix: missing [0] list indexing and inaccurate distance check.
            if num_bones >= 3:
                first_pos = self.obj.matrix_world @ sorted_bones_base[0].head
                last_pos = self.obj.matrix_world @ sorted_bones_base[-1].head
                edge_dist = (first_pos - last_pos).length

                total_dist = 0
                for i in range(num_bones - 1):
                    p1 = self.obj.matrix_world @ sorted_bones_base[i].head
                    p2 = self.obj.matrix_world @ sorted_bones_base[i + 1].head
                    total_dist += (p1 - p2).length

                avg_dist = total_dist / (num_bones - 1) if num_bones > 1 else 1.0
                if edge_dist < avg_dist * 2.5:  # slightly widened threshold for stable ring detection
                    is_ring = True

            if is_ring:
                # Key improvement: compute the ring's center using the
                # midpoint between each bone's head and tail, not just head.
                world_centers = []
                for b in bones:
                    w_head = self.obj.matrix_world @ b.head
                    w_tail = self.obj.matrix_world @ b.tail
                    world_centers.append((w_head + w_tail) * 0.5)

                center_x = sum(co.x for co in world_centers) / len(bones)
                center_y = sum(co.y for co in world_centers) / len(bones)

                # Key improvement: measure the 360-degree ring angle precisely
                # relative to each bone's midpoint center.
                def get_horizontal_angle(pb):
                    w_head = self.obj.matrix_world @ pb.head
                    w_tail = self.obj.matrix_world @ pb.tail
                    w_mid = (w_head + w_tail) * 0.5
                    dx = w_mid.x - center_x
                    dy = w_mid.y - center_y
                    return math.atan2(dy, dx)

                # Sort bones perfectly around the ring by angle (-PI to PI).
                sorted_ring_bones = sorted(bones, key=get_horizontal_angle)

                # Chain only adjacent neighbors in order, ring-style (prevents crossing links).
                for i in range(num_bones):
                    bone_A = sorted_ring_bones[i]
                    bone_B = sorted_ring_bones[(i + 1) % num_bones]  # last bone wraps back to the first

                    w_tail_A = self.obj.matrix_world @ bone_A.tail
                    w_tail_B = self.obj.matrix_world @ bone_B.tail
                    tail_dist = (w_tail_A - w_tail_B).length

                    # Ring structures need a looser link-distance threshold
                    # (raise above 5.0 if needed).
                    if tail_dist < 10.0:
                        self.tail_pairs.append((bone_A.name, bone_B.name, tail_dist))
            else:
                # Original linking logic for a flat, in-line bone structure.
                for i in range(num_bones - 1):
                    bone_A = sorted_bones_base[i]
                    bone_B = sorted_bones_base[i + 1]
                    w_tail_A = self.obj.matrix_world @ bone_A.tail
                    w_tail_B = self.obj.matrix_world @ bone_B.tail
                    tail_dist = (w_tail_A - w_tail_B).length
                    if tail_dist < 5.0:
                        self.tail_pairs.append((bone_A.name, bone_B.name, tail_dist))

    def _mesh_name(self):
        # Bug fix: the name used to be hardcoded to "Lattice_Mesh", so two or
        # more armatures overwrote each other's mesh/engine data. Include the
        # armature's name to make it unique.
        return f"Lattice_Mesh_{self.obj.name}"

    def create_lattice_mesh(self):
        mesh_name = self._mesh_name()
        existing_obj = bpy.data.objects.get(mesh_name)
        if existing_obj:
            self.mesh_obj = existing_obj
            self.mesh_obj["wiggle_target_armature"] = self.obj.name
            self.update_mesh_vertices()
            return

        if not self.tail_pairs:
            return

        mesh_data = bpy.data.meshes.new(name=f"Wiggle_Lattice_Mesh_Data_{self.obj.name}")
        verts = []
        edges = []

        for i, (name_A, name_B, _) in enumerate(self.tail_pairs):
            verts.append((0.0, 0.0, 0.0))
            verts.append((0.0, 0.0, 0.0))
            edges.append((i * 2, i * 2 + 1))

        mesh_data.from_pydata(verts, edges, [])
        mesh_data.update()

        self.mesh_obj = bpy.data.objects.new(name=mesh_name, object_data=mesh_data)
        self.mesh_obj["wiggle_target_armature"] = self.obj.name
        bpy.context.scene.collection.objects.link(self.mesh_obj)
        self.update_mesh_vertices()

    def update_mesh_vertices(self):
        try:
            if not self.mesh_obj or self.mesh_obj.name not in bpy.data.objects:
                return
            if not self.obj or self.obj.name not in bpy.data.objects:
                return
            pose_bones = self.obj.pose.bones
            mat_world = self.obj.matrix_world
            mesh_data = self.mesh_obj.data
        except ReferenceError:
            return

        if len(mesh_data.vertices) < len(self.tail_pairs) * 2:
            return

        for i, (name_A, name_B, _) in enumerate(self.tail_pairs):
            if name_A not in pose_bones or name_B not in pose_bones:
                continue

            w_tail_A = mat_world @ pose_bones[name_A].tail
            w_tail_B = mat_world @ pose_bones[name_B].tail

            mesh_data.vertices[i * 2].co = w_tail_A
            mesh_data.vertices[i * 2 + 1].co = w_tail_B

    def remove_lattice_mesh(self):
        try:
            old_objs = [o for o in bpy.data.objects if o.name.startswith("Lattice_Mesh")]
            for old_obj in old_objs:
                bpy.data.objects.remove(old_obj, do_unlink=True)
            old_meshes = [m for m in bpy.data.meshes if m.name.startswith("Wiggle_Lattice_Mesh_Data")]
            for old_mesh in old_meshes:
                bpy.data.meshes.remove(old_mesh, do_unlink=True)
        except:
            pass

    def evaluate_physics(self, context, current_stiffness, current_stretch):
        try:
            if not self.obj or self.obj.name not in bpy.data.objects:
                return False
            pose_bones = self.obj.pose.bones
            mat_world = self.obj.matrix_world
        except ReferenceError:
            return False

        self.update_mesh_vertices()

        if not context.screen.is_animation_playing:
            return True

        mat_world_inv = mat_world.inverted()

        # Fix: removed direct pb.scale manipulation - it collided with
        # wiggle_2's own stretch computation and warped bones.

        colliders_data = []
        for pb in pose_bones:
            # Bug fix: wiggle_collider_radius is now registered globally on
            # every bone, so hasattr() is always True - "or hasattr(...)"
            # would make every bone a collider regardless of the
            # wiggle_is_collider toggle. Judge only by the actual toggle
            # value.
            if getattr(pb, "wiggle_is_collider", False):
                c_loc = mat_world @ pb.head
                c_rad = getattr(pb, "wiggle_collider_radius", 0.03)
                colliders_data.append((c_loc, c_rad))

        for name_A, name_B, rest_dist in self.tail_pairs:
            if name_A not in pose_bones or name_B not in pose_bones:
                continue
            pb_A = pose_bones[name_A]
            pb_B = pose_bones[name_B]
            w_tail_A = mat_world @ pb_A.tail
            w_tail_B = mat_world @ pb_B.tail
            current_vec = w_tail_B - w_tail_A
            current_dist = current_vec.length
            if current_dist == 0:
                continue
            delta = current_dist - rest_dist
            pair_key = (name_A, name_B)

            # Bug fix: current_stretch used to be a dead parameter, accepted
            # but never used. Now actually used as the deadzone (tolerance)
            # size - the larger it is, the more stretch is allowed.
            tolerance = max(0.0005, current_stretch)
            if abs(delta) < tolerance:
                # Bug fix: self.damping_to_rest was also only ever assigned,
                # never used. A pair that no longer needs correction now
                # gradually decays its previous force instead of snapping
                # abruptly to zero.
                if pair_key in self.prev_force_dirs:
                    self.prev_force_dirs[pair_key] = self.prev_force_dirs[pair_key] * (1.0 - self.damping_to_rest)
                continue

            local_force_dir = (mat_world_inv.to_3x3() @ current_vec.normalized()) * delta
            collision_offset = mathutils.Vector((0, 0, 0))
            for c_loc, c_rad in colliders_data:
                to_tail_A = w_tail_A - c_loc
                if to_tail_A.length < c_rad and to_tail_A.length > 0:
                    collision_offset += to_tail_A.normalized() * (c_rad - to_tail_A.length)

                to_tail_B = w_tail_B - c_loc
                if to_tail_B.length < c_rad and to_tail_B.length > 0:
                    collision_offset += to_tail_B.normalized() * (c_rad - to_tail_B.length)

            if collision_offset.length > 0:
                local_col_dir = mat_world_inv.to_3x3() @ collision_offset
                local_force_dir += local_col_dir * 3.0

            # Bug fix: self.stretch_elasticity/self.prev_force_dirs were also
            # only ever assigned, never used. Blend with the previous
            # frame's force direction to prevent an abrupt snap.
            prev_dir = self.prev_force_dirs.get(pair_key, local_force_dir)
            blend_fac = max(0.0, min(1.0, 1.0 - self.stretch_elasticity))
            local_force_dir = prev_dir.lerp(local_force_dir, blend_fac)
            self.prev_force_dirs[pair_key] = local_force_dir

            bone_local_y = mathutils.Vector((0, 1, 0))
            target_direction = (bone_local_y + local_force_dir * current_stiffness * 0.5).normalized()
            rot_offset = bone_local_y.rotation_difference(target_direction)
            if pb_A.rotation_mode == 'QUATERNION':
                pb_A.rotation_quaternion = pb_A.rotation_quaternion @ rot_offset
            elif pb_A.rotation_mode == 'AXIS_ANGLE':
                # Bug fix: AXIS_ANGLE mode wasn't handled at all before, so the rotation got dropped.
                aa = pb_A.rotation_axis_angle
                cur_q = mathutils.Quaternion((aa[1], aa[2], aa[3]), aa[0])
                new_q = cur_q @ rot_offset
                axis, angle = new_q.to_axis_angle()
                pb_A.rotation_axis_angle = (angle, axis.x, axis.y, axis.z)
            else:
                pb_A.rotation_euler.rotate(rot_offset)
        return True

def update_lattice_show_debug(self, context):
    if not self.wiggle_lattice_show_debug:
        old_objs = [o for o in bpy.data.objects if o.name.startswith("Lattice_Mesh")]
        for old_obj in old_objs:
            try: old_obj.hide_viewport = True
            except: pass
    else:
        old_objs = [o for o in bpy.data.objects if o.name.startswith("Lattice_Mesh")]
        for old_obj in old_objs:
            try: old_obj.hide_viewport = False
            except: pass

    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()

def update_lattice_toggle(self, context):
    if self.wiggle_use_lattice:
        if WiggleRTX_ModalVisualOperator.is_running():
            return
        try:
            bpy.ops.wm.wiggle_rtx_horizontal_run('INVOKE_DEFAULT')
        except:
            pass
    else:
        try:
            old_objs = [o for o in bpy.data.objects if o.name.startswith("Lattice_Mesh")]
            for old_obj in old_objs:
                bpy.data.objects.remove(old_obj, do_unlink=True)
            old_meshes = [m for m in bpy.data.meshes if m.name.startswith("Wiggle_Lattice_Mesh_Data")]
            for old_mesh in old_meshes:
                bpy.data.meshes.remove(old_mesh, do_unlink=True)
        except:
            pass
        global _WIGGLE_RTX_GL_ENGINE
        _WIGGLE_RTX_GL_ENGINE = None

class WiggleRTX_ModalVisualOperator(bpy.types.Operator):
    bl_idname = "wm.wiggle_rtx_horizontal_run"
    bl_label = "Wiggle RTX Horizontal Visual Solver"
    _timer = None
    _is_running = False

    @classmethod
    def is_running(cls):
        return cls._is_running

    def modal(self, context, event):
        if not context.scene.wiggle_use_lattice:
            return self.cancel(context)
        if event.type == 'ESC':
            context.scene.wiggle_use_lattice = False
            return self.cancel(context)
        if event.type == 'TIMER':
            global _WIGGLE_RTX_GL_ENGINE
            if _WIGGLE_RTX_GL_ENGINE:
                try:
                    lattice_stiffness = getattr(context.scene, "wiggle_lattice_stiffness", 0.05)
                    lattice_stretch = getattr(context.scene, "wiggle_lattice_stretch", 0.01)
                    success = _WIGGLE_RTX_GL_ENGINE.evaluate_physics(context, lattice_stiffness, lattice_stretch)
                    if not success:
                        return self.cancel(context)
                except ReferenceError:
                    return self.cancel(context)
        return {'PASS_THROUGH'}

    def execute(self, context):
        return self.invoke(context, None)

    def invoke(self, context, event):
        if WiggleRTX_ModalVisualOperator._is_running:
            return {'FINISHED'}

        active_obj = None
        # Bug fix: the mesh name changed from "Lattice_Mesh" to
        # "Lattice_Mesh_<armature name>" (to avoid collisions between
        # multiple armatures), so lookup must match by prefix, not exact name.
        existing_mesh = next((o for o in bpy.data.objects if o.name.startswith("Lattice_Mesh_")), None)
        if existing_mesh and "wiggle_target_armature" in existing_mesh:
            active_obj = bpy.data.objects.get(existing_mesh["wiggle_target_armature"])

        if not active_obj:
            active_obj = context.view_layer.objects.active

        if not active_obj or active_obj.type != 'ARMATURE':
            self.report({'ERROR'}, "Select an Armature Object first!")
            return {'CANCELLED'}

        global _WIGGLE_RTX_GL_ENGINE
        _WIGGLE_RTX_GL_ENGINE = WiggleRTXHorizontalEngine(active_obj)

        if not context.scene.wiggle_lattice_show_debug:
            if _WIGGLE_RTX_GL_ENGINE.mesh_obj:
                _WIGGLE_RTX_GL_ENGINE.mesh_obj.hide_viewport = True

        wm = context.window_manager
        WiggleRTX_ModalVisualOperator._timer = wm.event_timer_add(0.01, window=context.window)
        wm.modal_handler_add(self)
        WiggleRTX_ModalVisualOperator._is_running = True
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        global _WIGGLE_RTX_GL_ENGINE
        wm = context.window_manager
        if WiggleRTX_ModalVisualOperator._timer:
            try:
                wm.event_timer_remove(WiggleRTX_ModalVisualOperator._timer)
            except:
                pass
            WiggleRTX_ModalVisualOperator._timer = None

        _WIGGLE_RTX_GL_ENGINE = None
        WiggleRTX_ModalVisualOperator._is_running = False
        return {'CANCELLED'}

@bpy.app.handlers.persistent
def auto_start_lattice_on_load(dummy):
    scene = bpy.context.scene
    if scene and getattr(scene, "wiggle_use_lattice", False):
        try:
            bpy.ops.wm.wiggle_rtx_horizontal_run('INVOKE_DEFAULT')
        except:
            pass

def register():
    try:
        bpy.utils.register_class(WiggleRTX_ModalVisualOperator)
    except ValueError:
        pass
    if auto_start_lattice_on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(auto_start_lattice_on_load)

    # Bug fix: wiggle_is_collider / wiggle_collider_radius, referenced by
    # evaluate_physics(), were never registered anywhere, so the
    # self-collision-avoidance feature could never actually turn on.
    # Register them as real properties.
    if not hasattr(bpy.types.PoseBone, "wiggle_is_collider"):
        bpy.types.PoseBone.wiggle_is_collider = bpy.props.BoolProperty(
            name="Lattice Collider",
            description="Nearby lattice pairs push away from this bone's head",
            default=False
        )
    if not hasattr(bpy.types.PoseBone, "wiggle_collider_radius"):
        bpy.types.PoseBone.wiggle_collider_radius = bpy.props.FloatProperty(
            name="Lattice Collider Radius", default=0.03, min=0.0
        )

    # Bug fix: wiggle_lattice_stretch was also passed a value in
    # evaluate_physics(), but was never registered as a Scene property, so
    # it always fell back to getattr's default (0.01).
    if not hasattr(bpy.types.Scene, "wiggle_lattice_stretch"):
        bpy.types.Scene.wiggle_lattice_stretch = bpy.props.FloatProperty(
            name="Lattice Stretch Tolerance",
            description="How much a lattice pair can deviate before being pulled back",
            default=0.01, min=0.0, max=1.0
        )

def unregister():
    if auto_start_lattice_on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(auto_start_lattice_on_load)
    try:
        bpy.utils.unregister_class(WiggleRTX_ModalVisualOperator)
    except (RuntimeError, ValueError):
        pass

    for attr in ("wiggle_is_collider", "wiggle_collider_radius"):
        if hasattr(bpy.types.PoseBone, attr):
            try: delattr(bpy.types.PoseBone, attr)
            except Exception: pass
    if hasattr(bpy.types.Scene, "wiggle_lattice_stretch"):
        try: delattr(bpy.types.Scene, "wiggle_lattice_stretch")
        except Exception: pass
