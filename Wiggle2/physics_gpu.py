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
    
    # 서브스텝 결정
    sub_steps = 4 if use_substeps else 1
    dt = base_dt / sub_steps

    obj = bpy.context.active_object
    if not obj: return
    
    world_to_obj = obj.matrix_world.inverted()
    # 관성 벡터 (delta_move의 반대 방향)
    inertia_vec = (world_to_obj.to_quaternion() @ (-delta_move))

    # 루트 본 초기 위치 고정
    _cache["pos"][0] = bones[0].head.copy()
    _cache["vel"][0] = Vector((0,0,0))

    # 1. 시뮬레이션 로직
    for _ in range(sub_steps):
        for i in range(1, n):
            b = bones[i]
            prev = _cache["pos"][i-1]
            pos  = _cache["pos"][i]
            vel  = _cache["vel"][i]

            # [수정] 강성 보정: 단순히 나누지 않고 서브스텝에 맞춰 감쇠율 조정
            stiff = getattr(b, "wiggle_stiff", 1.0)
            damp = getattr(b, "wiggle_damp", 0.1)
            gravity = getattr(b, "wiggle_gravity", 0.0)
            stretch = getattr(b, "wiggle_stretch", 1.0)
            rest_len = b.bone.length

            # 본의 정지 상태 방향 (Rest Direction)
            rest_dir = (b.tail - b.head)
            if rest_dir.length < 1e-8: rest_dir = Vector((0,0,1))
            else: rest_dir.normalize()

            # 목표 위치 (안테나처럼 형태 유지의 핵심)
            target = prev + rest_dir * rest_len

            # [물리 연산 수정] 
            # 1. 탄성력 적용
            vel += (target - pos) * (stiff * 10.0) * dt 
            
            # 2. 중력 적용
            vel += Vector((0,0,-gravity)) * dt
            
            # 3. [핵심 수정] 관성을 project하지 않고 전체 벡터로 적용하여 본이 말리는 현상 억제
            # 서브스텝당 관성 배분
            vel += (inertia_vec / sub_steps) * (1.0 / dt) * 0.1 # 가속도 보정
            
            # 4. 공기 저항 (Damping)
            vel *= (1.0 - damp * dt)
            
            # 위치 업데이트
            pos += vel * dt

            # [제약 조건] 길이 유지 (Stretching 대응)
            d = pos - prev
            if d.length < 1e-8: d = rest_dir
            else: d.normalize()

            max_len = rest_len * stretch
            pos = prev + d * min(rest_len, max_len)

            _cache["pos"][i] = pos
            _cache["vel"][i] = vel

    # 2. 회전 적용 (원본 방식 유지하되 안정성 강화)
    obj_inv = obj.matrix_world.inverted().to_3x3()
    
    for i in range(1, n):
        b = bones[i]
        parent_pos = _cache["pos"][i-1]
        current_pos = _cache["pos"][i]

        world_dir = (current_pos - parent_pos)
        if world_dir.length < 1e-8: continue
        world_dir.normalize()

        # 본의 로컬 Y축 방향 계산
        rest_dir_pose = b.bone.matrix_local.to_3x3() @ Vector((0,1,0))
        rest_dir_pose.normalize()

        target_dir_pose = obj_inv @ world_dir
        target_dir_pose.normalize()

        # 두 벡터 사이의 회전 차이 계산
        q = rest_dir_pose.rotation_difference(target_dir_pose)

        b.rotation_mode = 'QUATERNION'
        b.rotation_quaternion = q

    return True
