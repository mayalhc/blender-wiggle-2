import bpy
from mathutils import Vector

# 1. 전역 변수 선언 확인
_cache = {
    "pos": None,
    "vel": None,
    "count": 0,
}

# 2. 캐시 초기화 함수 (수정됨)
def reset_cache():
    global _cache  # 이 줄이 반드시 있어야 합니다.
    _cache["pos"] = None
    _cache["vel"] = None
    _cache["count"] = 0
    print(">>> RTX CACHE: RESET")
    
def calculate_parallel(bones, scene, delta_move):
    global _cache
    n = len(bones)
    if n == 0: return

    if _cache["count"] != n:
        _cache["pos"] = [Vector((0,0,0)) for _ in range(n)]
        _cache["vel"] = [Vector((0,0,0)) for _ in range(n)]
        _cache["count"] = n

    fps = max(scene.render.fps, 1)
    base_dt = 1.0 / fps
    sub_steps = 4
    dt = base_dt / sub_steps

    obj = bpy.context.active_object
    if not obj: return
    
    world_to_obj = obj.matrix_world.inverted()
    inertia = (world_to_obj.to_quaternion() @ (-delta_move))

    root_pos = bones[0].head.copy()
    _cache["pos"][0] = root_pos
    _cache["vel"][0] = Vector((0,0,0))

    # 1. 시뮬레이션 로직 (원본 방식 유지하며 서브스텝만 적용)
    for _ in range(sub_steps):
        for i in range(1, n):
            b = bones[i]
            prev = _cache["pos"][i-1]
            pos  = _cache["pos"][i]
            vel  = _cache["vel"][i]

            stiff   = getattr(b, "wiggle_stiff", 1.0)
            damp    = getattr(b, "wiggle_damp", 0.1)
            gravity = getattr(b, "wiggle_gravity", 0.0)
            stretch = getattr(b, "wiggle_stretch", 1.0)
            rest_len = b.bone.length

            rest_dir = (b.tail - b.head)
            if rest_dir.length < 1e-8: rest_dir = Vector((0,0,1))
            else: rest_dir.normalize()

            target = prev + rest_dir * rest_len

            # 원본 수식 그대로 유지 (dt만 서브스텝용 사용)
            vel += (target - pos) * stiff * dt
            vel += Vector((0,0,-gravity)) * dt
            vel += inertia.project(rest_dir) * dt
            vel *= (1.0 - damp * dt)
            pos += vel * dt

            d = pos - prev
            if d.length < 1e-8: d = rest_dir
            else: d.normalize()

            max_len = rest_len * stretch
            pos = prev + d * min(rest_len, max_len)

            _cache["pos"][i] = pos
            _cache["vel"][i] = vel

    # 2. 회전 적용 (사용자가 준 원본 방식 100% 동일하게 복구)
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
        b.rotation_quaternion = q # 부모 매트릭스 계산 없이 원본대로 직접 대입

    return True
