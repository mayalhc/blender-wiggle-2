import bpy
from mathutils import Vector

# 1. 전역 변수 선언 확인
_cache = {
    "pos": None,
    "vel": None,
    "count": 0,
}

# 2. 캐시 초기화 함수
def reset_cache():
    global _cache
    _cache["pos"] = None
    _cache["vel"] = None
    _cache["count"] = 0
    print(">>> RTX CACHE: RESET")
    
# [수정] 네 번째 인자 use_substeps를 추가하여 'unexpected keyword' 에러를 해결했습니다.
def calculate_parallel(bones, scene, delta_move, use_substeps=True):
    global _cache
    n = len(bones)
    if n == 0: return

    if _cache["count"] != n:
        _cache["pos"] = [b.head.copy() for b in bones]
        _cache["vel"] = [Vector((0,0,0)) for _ in range(n)]
        _cache["count"] = n

    fps = max(scene.render.fps, 1)
    base_dt = 1.0 / fps
    
    # [로직] use_substeps 값에 따라 서브스텝 결정 (RTX 켜면 4단계, 끄면 1단계)
    sub_steps = 4 if use_substeps else 1
    dt = base_dt / sub_steps

    obj = bpy.context.active_object
    if not obj: return
    
    world_to_obj = obj.matrix_world.inverted()
    inertia = (world_to_obj.to_quaternion() @ (-delta_move))

    # 루트 본 초기 위치 고정
    _cache["pos"][0] = bones[0].head.copy()
    _cache["vel"][0] = Vector((0,0,0))

    # 1. 시뮬레이션 로직 (서브스텝 분기 및 Stiffness 보정 적용)
    for _ in range(sub_steps):
        for i in range(1, n):
            b = bones[i]
            prev = _cache["pos"][i-1]
            pos  = _cache["pos"][i]
            vel  = _cache["vel"][i]

            # [핵심 보정] 서브스텝이 적용될 때만 stiff 값을 나누어 
            # 프리셋 수치(예: 50)가 과하게 적용되어 본이 튀는 현상을 방지합니다.
            stiff   = getattr(b, "wiggle_stiff", 1.0)
            if use_substeps:
                stiff /= sub_steps
                
            damp    = getattr(b, "wiggle_damp", 0.1)
            gravity = getattr(b, "wiggle_gravity", 0.0)
            stretch = getattr(b, "wiggle_stretch", 1.0)
            rest_len = b.bone.length

            rest_dir = (b.tail - b.head)
            if rest_dir.length < 1e-8: rest_dir = Vector((0,0,1))
            else: rest_dir.normalize()

            target = prev + rest_dir * rest_len

            # 물리 연산
            vel += (target - pos) * stiff * dt
            vel += Vector((0,0,-gravity)) * dt
            # 관성도 서브스텝에 맞춰 분산 적용
            vel += (inertia / sub_steps).project(rest_dir) * dt
            
            vel *= (1.0 - damp * dt)
            pos += vel * dt

            # 길이 제약 (Constraint)
            d = pos - prev
            if d.length < 1e-8: d = rest_dir
            else: d.normalize()

            max_len = rest_len * stretch
            pos = prev + d * min(rest_len, max_len)

            _cache["pos"][i] = pos
            _cache["vel"][i] = vel

    # 2. 회전 적용 (원본 방식 100% 유지)
    obj_inv = obj.matrix_world.inverted().to_3x3()
    
    for i in range(1, n):
        b = bones[i]
        parent_pos = _cache["pos"][i-1]
        current_pos = _cache["pos"][i]

        world_dir = (current_pos - parent_pos)
        if world_dir.length < 1e-8: continue
        world_dir.normalize()

        rest_dir_pose = b.bone.matrix_local.to_3x3() @ Vector((0,1,0))
        rest_dir_pose.normalize()

        target_dir_pose = obj_inv @ world_dir
        target_dir_pose.normalize()

        q = rest_dir_pose.rotation_difference(target_dir_pose)

        b.rotation_mode = 'QUATERNION'
        b.rotation_quaternion = q

    return True
