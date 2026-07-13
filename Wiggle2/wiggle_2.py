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
    # 1. 해당 오브젝트의 위글 데이터를 가져옴
    wo = bpy.context.scene.wiggle.list.get(ob.name)

    # 2. 데이터가 존재하는지(None이 아닌지) 먼저 확인
    if wo and hasattr(wo, 'list'):
        arm_obj = bpy.data.objects.get(wo.name)
        if arm_obj and arm_obj.pose:
            bones = [arm_obj.pose.bones.get(wb.name) for wb in wo.list]
            reset_bones_batch(bones)
    else:
        # 데이터가 없으면 에러 없이 조용히 넘어감
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

    # 1. 모든 본의 시각적 포즈를 원래 자리로 리셋
    for b in bones:
        b.matrix_basis = Matrix.Identity(4)
        b.id_data.update_tag()

    # 2. 전체 배치에 대해 딱 한 번만 월드 행렬 재계산
    #    (본 개수만큼 반복 호출하면 그만큼 전체 뎁스그래프를 다시 평가해서
    #    본이 많은 리그에서 매우 느려짐)
    bpy.context.view_layer.update()

    zero_v = Vector((0, 0, 0))
    for b in bones:
        # 3. 업데이트된 본의 실제 월드 위치 확보
        world_mat = b.id_data.matrix_world
        current_world_matrix = world_mat @ b.matrix

        # 4. 물리 좌표를 현재 본의 '실제 월드 좌표'로 완벽 동기화
        curr_tail_pos = (world_mat @ b.matrix @ Matrix.Translation(Vector((0, b.bone.length, 0)))).translation
        curr_head_pos = current_world_matrix.translation

        # 5. 가속도와 속도를 0으로 완전히 죽임 (날아가는 것 방지)
        b.wiggle.position = b.wiggle.position_last = curr_tail_pos
        b.wiggle.position_head = b.wiggle.position_last_head = curr_head_pos

        b.wiggle.velocity = b.wiggle.velocity_head = zero_v
        b.wiggle.collision_normal = b.wiggle.collision_normal_head = zero_v

        # 6. 물리 연산용 행렬 데이터도 현재 리셋된 값으로 덮어씀
        b.wiggle.matrix = flatten(current_world_matrix)

        # 7. 마지막으로 update_matrix를 호출하여 연산 준비 완료
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
    """선택된 포즈본에 같은 값을 전파. b[prop] 방식 제거 → setattr 통일."""
    if context.scene.get("wiggle_updating"):
        return
    val = getattr(self, prop)
    context.scene["wiggle_updating"] = True
    try:
        if isinstance(self, bpy.types.PoseBone):
            selected = list(getattr(context, "selected_pose_bones", None) or [])
            # [버그 수정] "b is self"(정체성 비교)를 썼는데, Blender는
            # context.selected_pose_bones로 얻은 본과 update 콜백의 self가
            # 같은 본이어도 서로 다른 Python 래퍼 객체일 수 있다(다른
            # NLA/PropertyGroup 관련 코드에서도 같은 문제를 발견/수정함).
            # 그러면 "이미 값이 설정된 self"를 못 알아보고 다시
            # setattr해서 자기 자신에게 여러 본 선택 시 예상 못 한 순서로
            # 값이 되돌아오거나, reset_bone()이 활성 본에는 절대 안 불리는
            # 등 본마다 결과가 달라 보이는 문제가 있었다. 이름 비교로
            # 바꾼다.
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

# Sim Mix Layers의 Layer Weight/Sim Mix 슬라이더가 실제 프레임을 바꾸지
# 않고도 즉시 미리보기를 갱신할 수 있도록, 마지막 실제 프레임에서 계산된
# (애니메이션 회전, 시뮬레이션 회전, rotation_mode, 애니메이션 위치, 시뮬
# 레이션 위치)를 본별로 저장해둔다. 키: (오브젝트 이름, 본 이름).
# refresh_influence_blend()에서 소비.
_LAST_BLEND_CACHE = {}


def refresh_influence_blend(obj):
    """실제 프레임을 바꾸지 않고(=물리를 다시 계산하지 않고), 마지막으로
    계산된 애니메이션/시뮬레이션 포즈를 현재 wiggle_influence 값으로 다시
    블렌드해서 뷰포트에 즉시 반영한다. Sim Mix Layers의 Layer Weight/Sim
    Mix 슬라이더를 움직였을 때(타임라인은 그대로) 호출한다. 물리나 프레임을
    건드리지 않으므로 리셋/속도 손실 같은 부작용이 전혀 없다."""
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
    """get_parent()의 결과는 한 프레임 안에서는 절대 바뀌지 않는데(같은 프레임
    안에서 wiggle_tail/head/mute가 바뀔 일이 없음), constrain()이 본마다
    iterations번씩 호출되면서 매번 부모 체인을 다시 타고 올라가 재계산하고
    있었음. 프레임 시작 시점에 캐시를 비우고, 그 프레임 안에서는 재사용."""
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
    # 애널리틱 프리미티브(Sphere/Box/Cylinder/Capsule) 모드에서는 메쉬가 필요
    # 없으므로 어떤 오브젝트든(Empty 포함) 트랜스폼 참조로 쓸 수 있게 허용.
    ctype = getattr(self, "wiggle_collider_type", 'Object')
    if ctype in PRIMITIVE_COLLIDERS:
        return True
    return object.type == 'MESH'

WIND_FIELD_TYPES = {'WIND', 'TURBULENCE', 'VORTEX'}

def wind_poll(self, object):
    # [기능 확장] 원래 WIND 타입만 허용했음. Turbulence/Vortex도 지원.
    return object.field and object.field.type in WIND_FIELD_TYPES


def compute_wind_force(wind_ob, pos, ref_dir, mass, wind_mult):
    """wind_ob의 Force Field 타입(Wind/Turbulence/Vortex)에 따라 실제 힘을 계산.
    mass로 나눈 가속도 형태로 반환 (F/mass), 호출부에서 바로 dt^2와 곱해 쓰면 됨."""
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
        # 4D 노이즈가 없으므로 프레임 번호로 Z축을 오프셋시켜 정지된 본도
        # 시간에 따라 힘이 흔들리도록 근사함 (실제 Turbulence 필드처럼).
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
# 애널리틱 프리미티브 콜라이더 (Sphere/Box/Cylinder/Capsule)
# 메쉬 없이 오브젝트의 트랜스폼(위치/회전/스케일)만으로 충돌 판정.
# 전부 "단위 프리미티브"를 기준으로 계산하고 오브젝트 스케일로 실제 크기를
# 조절하는 방식 (Blender 기본 프리미티브와 동일한 크기 컨벤션):
#   Sphere   : 반지름 1
#   Box      : 반높이(half-extent) 1 (기본 Cube, 2x2x2)
#   Cylinder : 반지름 1, 반높이 1, Z축 방향 (기본 Cylinder, 반지름1 높이2)
#   Capsule  : 반지름 1, 몸통 반높이 1, Z축 방향
# 각 함수는 오브젝트 로컬 스페이스의 점을 받아 (로컬 최근접점, 로컬 노멀)을 반환.
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
        # 안에 있으면 가장 가까운 면으로 밀어냄
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
# 셀프 콜리전 (opt-in, 오브젝트 단위) - 진짜 캡슐(선분)-캡슐 충돌.
# 각 wiggle_tail 본을 head-tail 선분(반지름 wiggle_radius)으로 보고
# 표준 "두 3D 선분 사이 최근접점" 알고리즘으로 실제 거리를 계산함
# (Ericson, Real-Time Collision Detection). 점 기반보다 정확 - 예를 들어
# 두 본의 중간 부분끼리 스치듯 지나가는 경우도 잡아냄.
# 직접 이어진 부모-자식 쌍은 원래 붙어있어야 하므로 검사에서 제외 - 안
# 그러면 stiffness/stretch 제약과 계속 싸워서 떨림이 생김.
# 프레임당 정확히 한 번만 적용(반복마다 X)해서 진동 위험을 최소화함.
# 참고: wiggle_head만 켜져 있고 wiggle_tail은 꺼진 "떠 있는 점" 본은
# 선분을 이룰 수 없어 이 검사에서 제외됨 (드문 케이스).
# ============================================================

def _closest_seg_seg(p1, q1, p2, q2):
    """두 3D 선분(p1-q1, p2-q2) 사이의 최근접점 쌍과 파라미터(s,t, 0=시작점
    1=끝점)를 반환. 표준 알고리즘."""
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
    """본을 캡슐(선분)로 볼 때의 head/tail 월드 좌표. wf_head가 켜져 있으면
    시뮬레이션된 헤드 위치, 아니면 현재 애니메이션된(고정된) 헤드 위치."""
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

    # 직접 이어진 (부모-자식) 본 쌍은 건너뛸 목록 미리 계산
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

            # s/t: 0=head쪽, 1=tail쪽 - 충돌이 일어난 쪽 끝점을 그 비중만큼 밀어냄.
            # head가 애니메이션 고정(비-floating)이면 이동시킬 수 없으므로
            # 전량 tail로 보정함.
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

            # [버그 수정] bounce/vel이 추출만 되고 한 번도 쓰인 적이 없어서
            # "Bounce" 설정이 처음부터 아무 효과가 없었음. 충돌 노멀 방향
            # 속도 성분을 실제로 반사시켜줌 (튕기는 만큼 bounce로 조절).
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

    # [버그 수정 계속] 이 시스템은 속도를 매 프레임 끝에서 position과
    # position_last의 차이로 다시 계산하기 때문에(verlet 방식), vel을 직접
    # 바꿔도 프레임이 끝나면 그대로 덮어써져서 아무 효과가 없었음. 실제로
    # 반사가 다음 프레임까지 이어지려면 position_last를 같이 조정해야 함.
    # move()에서 호출할 때만(register_bounce=True) 적용 - constrain()의
    # 반복 보정 호출까지 매번 적용하면 매 iteration마다 반사가 겹쳐 계산돼
    # 불안정해질 수 있어서 프레임당 진짜 충돌 1회(move 단계)에서만 반영.
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
            # 5.0 대응: 스케일 오염 방지
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
    
    # --- 스케일(sy) 계산 ---
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
            # 변경 코드:
            dist = ((b.id_data.matrix_world @ b.matrix).to_translation() - b.wiggle.position).length
            sy = dist / l_world if l_world > 0.0001 else 1.0
    
    if b.wiggle_head and not b.bone.use_connect:
        dist = (b.wiggle.position_head - b.wiggle.position).length
        sy = dist / l_world if l_world > 0.0001 else 1.0
            
    # [수정 핵심] Stretch 설정이 0(또는 매우 작음)일 때 시각적 늘어남 원천 차단
    # 0.999~1.001 클램프 대신, Stretch를 안 쓸 거라면 무조건 1.0으로 고정합니다.
    if hasattr(b, 'wiggle_stretch') and b.wiggle_stretch < 0.01:
        sy = 1.0
    else:
        # Stretch를 사용할 때도 비정상적인 발산(너무 길어짐)을 막기 위한 안전장치
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
            
    # 최종 행렬 정규화 및 스케일 오염 방지
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
        # 5.0 대응: 영향력(Influence) 계산 시 수치 안정화
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
        
        # 메쉬 데이터를 안전하게 업데이트하기 위한 블렌더 표준 방식
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
        mesh.update()  # 💡 블렌더에게 메쉬가 변경되었음을 알려 화면을 갱신합니다.

# END OF REVISION #

# START OF REVISION #

# ====================== 1. apply_angle_limits (완전 안정 버전 - 변경 없음) ======================
def apply_angle_limits(b, h_pos, q_basis, rest_dir, world_rest_length):
    from math import atan2, cos, sin, radians, acos
    from mathutils import Vector, Quaternion

    curr_vec = b.wiggle.position - h_pos
    if curr_vec.length < 1e-6:
        return h_pos + rest_dir * world_rest_length

    wiggle_angle_limit = getattr(b, "wiggle_angle_limit", 180)
    if wiggle_angle_limit < 179.5:
        limit_rad = radians(wiggle_angle_limit)
        
        # 1. 안전하게 두 벡터를 모두 정규화합니다.
        rest_dir_norm = rest_dir.normalized()
        curr_dir = curr_vec.normalized()
        
        # 2. 부동소수점 오차 방지 clamp
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
    # 2. 개별 리미트 (X / Z) — Cone 이후 적용
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
    """apply_angle_limits()는 move() 안에서만 호출되는데, move() 뒤에
    돌아가는 constrain()의 반복 거리-제약 솔버는 각도 제한을 전혀 모르기
    때문에 매 iteration마다 클램프된 위치를 다시 밖으로 끌고 나갈 수 있음
    (예: Total Limit 10도인데 실제로는 90도까지 벌어지던 버그).
    constrain()의 반복이 전부 끝난 뒤 프레임당 한 번, 최종 위치를 다시
    재클램프해서 실제로 각도 제한이 지켜지게 한다. constrain() 내부 로직은
    건드리지 않음."""
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
    # ↓↓↓ 실제 시뮬레이션 (dt > 0) ↓↓↓
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

    # [버그 수정] wiggle_tail_mute/wiggle_head_mute가 등록만 되고 어디서도
    # 읽힌 적이 없어서 UI에도 없고 아무 효과가 없었음. 켜져 있으면 물리
    # 계산을 건너뛰고 애니메이션된 레스트 위치를 그대로 따라가게 함
    # (본 자체를 체인에서 완전히 빼는 게 아니라, 그 쪽만 힘 계산을 멈춤).
    if b.wiggle_tail and getattr(b, 'wiggle_tail_mute', False):
        h_pos = b.wiggle.position_head if (b.wiggle_head and not b.bone.use_connect) else h_pos_anim
        b.wiggle.position = h_pos + rest_dir * world_rest_length
        b.wiggle.velocity = Vector((0, 0, 0))
        b.wiggle.position = apply_angle_limits(b, h_pos, q_basis, rest_dir, world_rest_length)
        pin(b)
        collide(b, dg, register_bounce=True)
    elif b.wiggle_tail:
        old_pos = b.wiggle.position.copy()

        # [버그 수정] adaptive_damp_mod가 매 프레임 계산만 되고 어디서도 읽히지
        # 않아서 Safety Guard가 실제로는 항상 아무 효과가 없었음(감쇠에 전혀
        # 반영 안 됨). 실제 감쇠 계산에 더해줌.
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
        # [버그 수정] wiggle_wind_ob_head/wiggle_wind_head가 등록되고 UI에도
        # 노출돼 있었지만 여기서 한 번도 적용된 적이 없었음 (Tail 바람만 작동).
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
    # [성능] dt/iterations는 프레임 안에서 불변인데 이 함수가 본마다
    # iterations번씩 호출되면서 매번 bpy.context.scene.wiggle을 다시 읽고
    # 있었음. 호출부(wiggle_post)에서 한 번 읽어 넘겨주면 재사용, 다른
    # 곳에서 인자 없이 부르면 기존처럼 안전하게 폴백.
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
            # [핵심 수정] 5.0 스케일 폭주 방지: 스케일을 무조건 (1,1,1)로 강제 고정
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
            # [핵심 수정] b.matrix.decompose()[2] 대신 Vector((1,1,1)) 사용
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
                        # 찌그러짐 방지: sc(스케일)가 1에서 너무 벗어나지 않도록 클램프
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
                    # 길이 정규화 보강
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
                        sc = max(0.99, min(1.01, sc)) # 찌그러짐 방지
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
            # 5.0 강제 정규화
            p.matrix = p.matrix.to_3x3().normalized().to_4x4()
            p.matrix.translation = p.matrix.translation
            
        if b.wiggle_tail:
            pin(b)
            collide(b, dg)
        if b.wiggle_head:
            collide(b, dg, True)
            
    update_matrix(b)
    # 5.0 강제 정규화
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

    # [성능] get_parent() 결과는 프레임 안에서 불변이므로 프레임마다 한 번만
    # 비움 - constrain()이 본마다 iterations번씩 호출하며 매번 부모 체인을
    # 다시 타고 올라가 재계산하던 중복을 없앰.
    clear_parent_cache()

    lastframe = w.lastframe

    # [기능 수정] "Loop Physics" 토글이 (scene.wiggle.loop, scene.wiggle_use_loop)
    # 두 개로 중복 등록돼 있었고 둘 다 실제로는 아무 데도 읽히지 않아 효과가
    # 없었음. scene.wiggle_use_loop 하나로 통일하고 실제로 연결함: 재생 중에
    # 타임라인이 끝에서 처음으로 자연스럽게 넘어가는 경우(진짜 루프)에만
    # 하드 리셋을 건너뛰어 물리 상태(속도 등)가 끊기지 않고 이어지게 함.
    # 사용자가 직접 앞으로 되감은 경우(수동 스크러빙)는 루프가 아니므로
    # 토글과 무관하게 항상 리셋됨.
    loop_enabled = getattr(scene, "wiggle_use_loop", False)
    is_playing = bool(bpy.context.screen and bpy.context.screen.is_animation_playing)
    is_natural_loop = loop_enabled and is_playing and cf <= scene.frame_start and lastframe >= scene.frame_end

    # 2. [리셋 로직] - 전체 본 순회 대신 위글 리스트 본들만 정확히 타겟팅
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

            # [수정] 전체 본 대신 리스트에 있는 본들만 업데이트하여 충돌 방지
            for wb in wo.list:
                b = ob.pose.bones.get(wb.name)
                if b: update_matrix(b, True)
                
        w.lastframe = cf
        w.reset = False
        return

    # 3. 시간 계산 (원본 수치 유지: fe를 곱하지 않음)
    w.dt = (1.0 / max(1.0, scene.render.fps))
    w.lastframe = cf

    # 4. 메인 루프
    for wo in w.list:
        ob = scene.objects.get(wo.name)
        # [버그 수정] wiggle_freeze가 베이크 후 True로 설정되지만 여기서
        # 한 번도 확인되지 않아서, 베이크된 키프레임 위에 물리가 계속
        # 돌면서 결과를 덮어쓰고 있었음.
        if not ob or ob.wiggle_mute or getattr(ob, "wiggle_freeze", False): continue

        # [기능 추가] 디스크 포인트 캐시 - 이미 계산된 프레임이면 재시뮬레이션
        # 없이 그대로 불러와서 적용 (타임라인 스크러빙 시 frame_start부터
        # 매번 다시 시뮬레이션하지 않아도 됨).
        cache_enabled = getattr(scene, "wiggle_cache_enable", False)
        if cache_enabled and wiggle_cache.has_frame(scene, ob.name, cf):
            if wiggle_cache.load_frame(scene, ob, wo):
                for wb in wo.list:
                    b = ob.pose.bones.get(wb.name)
                    if b: update_matrix(b, True)
                ob.update_tag()
                continue

        # Safety Boost (아마추어 개별 계산 유지)
        safety_boost = 0.0
        if getattr(scene, "wiggle_adaptive_damping", False):
            sensitivity = getattr(scene, "wiggle_safety_threshold", 10.0)

            # 1. 이동 속도 감지 (원본)
            if not hasattr(w, "last_ob_pos"): w.last_ob_pos = {}
            current_pos = ob.matrix_world.translation.copy()
            last_pos = w.last_ob_pos.get(ob.name, current_pos)
            delta_move = current_pos - last_pos
            obj_speed = delta_move.length / w.dt if w.dt > 0 else 0
            threshold = 5.0
            if obj_speed > threshold:
                safety_boost = (obj_speed - threshold) * sensitivity
            w.last_ob_pos[ob.name] = current_pos

            # 2. [추가] 회전 속도 감지 - 캐릭터가 휙 돌아설 때는 위치는 거의 안
            # 바뀌어도 본 끝(꼬리, 머리카락)의 실제 이동 거리는 매우 커서
            # 위치 기반 감지만으로는 못 잡음.
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
        # [버그 수정] wiggle_influence 블렌드가 회전만 되돌리고 위치는
        # 전혀 되돌리지 않아서, 헤드가 플로팅인(use_connect 아님) 본은
        # influence를 0으로 내려도 물리가 옮겨놓은 위치에 그대로 남아
        # 코일처럼 튀어나와 보이던 문제. 애니메이션 원본 위치도 함께 캐시.
        orig_locs = {b.name: b.location.copy() for b in active_bones}
        # Stretch로 인한 스케일도 회전/위치와 마찬가지로 되돌려야 함
        # (안 그러면 influence=0이어도 늘어난/찌그러진 모양이 남음).
        orig_scales = {b.name: b.scale.copy() for b in active_bones}

        for b in active_bones:
            if hasattr(b.wiggle, "adaptive_damp_mod"):
                b.wiggle.adaptive_damp_mod = safety_boost
            move(b, dg)
            
        for i in range(max(1, w.iterations)):
            for b in active_bones:
                constrain(b, w.iterations - 1 - i, dg, w.dt, w.iterations)

        # [기능 추가] 셀프 콜리전 - 기본 꺼짐, 오브젝트별 옵트인.
        # 반복(iteration)마다가 아니라 프레임당 딱 한 번만 적용해서 기존
        # stiffness/stretch 제약과 진동하며 싸우는 걸 방지.
        if getattr(ob, "wiggle_self_collide", False):
            apply_self_collision(active_bones, getattr(ob, "wiggle_self_collide_margin", 0.0))

        # [버그 수정] Angle Limit(Total Limit)이 move()에서만 적용되고 그 뒤에
        # 도는 constrain() 반복 솔버는 각도 제한을 모른 채 위치를 다시 끌고
        # 나가서, 예를 들어 10도로 설정해도 실제로는 훨씬 크게(예: 90도) 벌어
        # 지던 문제. 모든 constrain() 반복이 끝난 뒤 한 번 더 재클램프.
        for b in active_bones:
            reclamp_angle_limit(b)

        for b in active_bones:
            update_matrix(b, True)
            inf = getattr(b, "wiggle_influence", 1.0)

            # [버그 수정] Sim Mix Layers의 Layer Weight/Sim Mix가 최종 포즈에
            # 전혀 반영되지 않던 문제. (1) b.rotation_quaternion에만 썼는데,
            # rotation_mode가 QUATERNION이 아니면(대부분의 게임/UE5용
            # 리그는 Euler를 씀) Blender가 그 채널을 아예 평가에 쓰지 않아서
            # 조용히 무시됨. (2) 그 뒤에 다시 부른 update_matrix(b, True)가
            # rotation_quaternion을 전혀 읽지 않고 b.wiggle.position만으로
            # 행렬을 다시 계산해서, 방금 블렌드한 값을 곧바로 덮어써버렸음
            # (QUATERNION 모드여도 무효). matrix_basis에서 실제 시뮬레이션
            # 회전을 읽어 블렌드하고, 본의 실제 rotation_mode에 맞는 채널에
            # 써서 두 문제를 모두 해결. 이후 update_matrix()는 다시 부르지
            # 않음(부르면 방금 쓴 채널을 무시하고 물리 상태로 또 덮어씀).
            sim_q = b.matrix_basis.to_quaternion()
            anim_q = orig_rots.get(b.name, Quaternion((1, 0, 0, 0)))

            # [버그 수정] 회전만 블렌드하고 위치는 그대로 뒀던 문제.
            # wiggle_head가 플로팅(use_connect 아님)인 본은 update_matrix()가
            # b.location도 물리 위치로 직접 써버리는데, 회전만 애니메이션
            # 쪽으로 되돌려도 위치는 여전히 물리 결과에 남아있어서 influence
            # 를 0으로 내려도 본이 코일처럼 튀어나온 채로 남아있었음. 위치도
            # 똑같이 애니메이션 원본 쪽으로 되돌림.
            sim_loc = b.location.copy()
            anim_loc = orig_locs.get(b.name, sim_loc)
            sim_scale = b.scale.copy()
            anim_scale = orig_scales.get(b.name, sim_scale)

            # Sim Mix Layers가 실제 프레임을 바꾸지 않고도(슬라이더만
            # 만졌을 때) 새 influence로 즉시 다시 블렌드할 수 있도록, inf
            # 값과 무관하게 이번 프레임의 순수 애니메이션/시뮬레이션
            # 회전·위치·스케일을 항상 캐시해둔다.
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

        # 5. 속도 업데이트 (원본 수치 유지: fe로 나누지 않음)
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

        # 하드코딩된 속성 목록 대신 "wiggle_" 접두사를 가진 모든 프로퍼티를
        # 자동으로 순회해서 복사합니다. 새 속성이 추가돼도 이 목록이 낡아서
        # 누락되는 일이 없습니다.
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
        # 1. 물리 엔진 일시 정지 신호
        context.scene.wiggle.reset = True

        # 2. 리셋 실행 - 씬 전체 본을 모아 한 번에 배치 리셋
        #    (본마다 개별 view_layer.update()를 부르면 본이 많은 리그에서
        #    매우 느려짐 - reset_bones_batch가 전체에 대해 한 번만 갱신)
        for wo in context.scene.wiggle.list:
            ob = context.scene.objects.get(wo.name)
            if ob:
                bones = [ob.pose.bones.get(wb.name) for wb in wo.list]
                reset_bones_batch(bones)

        # 3. 마지막 연산 프레임을 현재로 고정하여 핸들러의 추적 방지
        context.scene.wiggle.lastframe = context.scene.frame_current
        context.scene.wiggle.reset = False
        
        # 5. 화면 강제 새로고침
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
                # Blender 버전에 따라 PoseBone.select / Bone.select 둘 다
                # 없을 수 있어 방어적으로 처리
                if hasattr(b, "select"):
                    b.select = True
                if hasattr(b.bone, "select"):
                    b.bone.select = True
                if first_bone is None:
                    first_bone, first_ob = b, ob

        # [버그 수정] 본들을 새로 선택만 하고 활성 본(active bone)은 전혀
        # 안 바꿔서, 클릭하기 전에 활성 본이 우연히 위글과 무관한(예:
        # Tail이 꺼진) 본이었으면 그 상태가 그대로 남아있었다. 그러면
        # 속성 패널은 방금 선택한 본들이 아니라 그 옛날 활성 본의 값을
        # 계속 보여주고, 거기서 체크박스를 누르면 update_prop()이 그 값을
        # (진짜로) 선택된 본들 전체로 전파하면서 엉뚱한 본까지 같이
        # 켜지는/꺼지는 결과가 났다. 방금 선택한 본 중 하나를 활성 본으로
        # 지정해서 패널이 항상 실제로 선택된 본의 값을 보여주게 한다.
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
        # 1. 초기 설정
        obj = context.active_object
        scene = context.scene
        wiggle_props = scene.wiggle
        
        start_frame = scene.frame_start
        end_frame = scene.frame_end
        original_frame = scene.frame_current
        duration = end_frame - start_frame
        # 루프 보정 구간 (마지막 20%)
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

        if not wiggle_props.bake_overwrite:
            new_action = bpy.data.actions.new(name="WiggleAction")
            obj.animation_data.action = new_action
        
        # 2. Preroll 안정화
        scene.wiggle.is_preroll = True
        preroll_start = start_frame - max(1, wiggle_props.preroll)
        for f in range(preroll_start, start_frame):
            scene.frame_set(f)
            context.view_layer.update()
        scene.wiggle.is_preroll = False

        # 3. 메인 베이크 루프
        for f in range(start_frame, end_frame + 1):
            scene.frame_set(f)
            context.view_layer.update()

            for pbone in bake_bones:
                pbone.keyframe_insert(data_path="location", group=pbone.name)
                if pbone.rotation_mode == 'QUATERNION':
                    pbone.keyframe_insert(data_path="rotation_quaternion", group=pbone.name)
                elif pbone.rotation_mode == 'AXIS_ANGLE':
                    pbone.keyframe_insert(data_path="rotation_axis_angle", group=pbone.name)
                else:
                    pbone.keyframe_insert(data_path="rotation_euler", group=pbone.name)
                pbone.keyframe_insert(data_path="scale", group=pbone.name)

        # 4. [핵심] Seamless Loop 보정 (AttributeError 수정 포함)
        if obj.animation_data and obj.animation_data.action:
            action = obj.animation_data.action
            bone_names = {b.name for b in bake_bones}
            
            # Blender 4.0+ .curves / 하위 버전 .fcurves 호환 처리
            curves = getattr(action, "curves", getattr(action, "fcurves", []))
            
            for fc in curves:
                if any(f'["{name}"]' in fc.data_path for name in bone_names):
                    # 시작 프레임 값 캡처
                    start_val = fc.evaluate(start_frame)
                    
                    # 루프 블렌딩 실행
                    if blend_frames > 0:
                        for f in range(end_frame - blend_frames, end_frame + 1):
                            t = (f - (end_frame - blend_frames)) / blend_frames
                            current_val = fc.evaluate(f)
                            # 끝값을 시작값으로 서서히 보간
                            blended_val = current_val * (1.0 - t) + start_val * t
                            fc.keyframe_points.insert(f, blended_val, options={'FAST'})

                    # 핸들 정리
                    for kp in fc.keyframe_points:
                        kp.interpolation = 'BEZIER'
                        kp.handle_left_type = 'AUTO'
                        kp.handle_right_type = 'AUTO'
                    fc.update()

        # 5. 마무리 및 복구
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
                # 생성 시 초기 정점들을 Head(0)와 Tail(offset)로 분리 배치
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
                
                # 본에 페어런팅 및 트랜스폼 정렬
                g.parent, g.parent_type, g.parent_bone = arm, 'BONE', pb.name
                g.matrix_local = Matrix.Identity(4)
                g.rotation_euler = (1.570796, 0, 0) # 본 진행방향 정렬
                
                # 초기 모양 보정 함수 호출
                WiggleToggleBBox.update_mesh_shape(pb, context)

        # 포즈 모드 복귀를 위한 활성 오브젝트 재설정
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
        # 💡 [치명적 버그 수정] 단일 active_pose_bone 대신, 드래그로 다중 선택된 모든 본들(selected_bones)을 가져옵니다.
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
            
        # 프리셋 종류별 수치 매칭 파싱
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
            
        # 💡 [정밀 수정] 루프를 돌며 드래그 선택된 본 전체에 해당 물리 설정을 강제 대입시킵니다.
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
    # Adaptive Safety Guard 내부 계산값
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
    # wiggle_angle_limit 는 ui_panel.py 에서 등록(정밀도 등 최종 정의).
    # 여기서 중복 등록하면 등록 순서에 따라 서로 덮어써서 unregister()가
    # 꼬일 수 있으므로 제거.

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
    # [버그 수정] hasattr(bpy.types, cls.__name__)는 Operator/Panel 클래스가
    # 실제로 등록됐는지 전혀 반영하지 못한다(Blender 5.x에서 실측 확인 -
    # register_class/bpy.ops는 정상 동작하는데도 hasattr는 등록 전후 내내
    # False). 그래서 이 가드로는 "이미 등록됨"을 절대 걸러내지 못해
    # 재등록(Reload Scripts, 비활성화 없이 다시 활성화 등) 시
    # "already registered" 예외가 났다. try/except로 실제 예외를 기준으로
    # 판단해야 한다.
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            pass

    bpy.types.PoseBone.wiggle = bpy.props.PointerProperty(type=WiggleBone, override={'LIBRARY_OVERRIDABLE'})
    bpy.types.Object.wiggle = bpy.props.PointerProperty(type=WiggleObject, override={'LIBRARY_OVERRIDABLE'})
    bpy.types.Scene.wiggle = bpy.props.PointerProperty(type=WiggleScene, override={'LIBRARY_OVERRIDABLE'})

    # 포인트 캐시 (디스크)
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
    # wiggle_influence 는 wiggle_layers.py 에서 등록 (default=1.0).
    # 여기서 중복 등록하면 default 값이 덮어씌워지므로 제거.
    # wiggle_use_lattice / wiggle_lattice_stiffness / wiggle_lattice_show_debug
    # 는 __init__.py 에서 wiggle_lattice_visual 모듈과 함께 등록됨.

def unregister():
    # [버그 수정] PointerProperty(bpy.types.*.wiggle 등)가 WiggleBone/
    # WiggleObject/WiggleScene PropertyGroup 클래스를 참조하고 있는 동안
    # 그 클래스를 먼저 unregister_class 하면 예외가 나서, reversed(classes)
    # 루프가 중간에 멈춰버리고 그 뒤에 있는 클래스(WiggleToggleBBox 등)는
    # 영원히 등록 해제되지 않는다 - 애드온을 끄고 다시 켜면(re-register)
    # "already registered as a subclass" 오류가 남. 그래서 프로퍼티(특히
    # PointerProperty)는 클래스를 해제하기 전에 먼저 지워야 한다.
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
    # [버그 수정] register()와 같은 이유로 hasattr 가드를 제거하고
    # try/except로 바꾼다 - hasattr(bpy.types, cls.__name__)가 항상 False라
    # 이 가드로는 unregister_class가 사실상 한 번도 호출되지 않고 있었다.
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
