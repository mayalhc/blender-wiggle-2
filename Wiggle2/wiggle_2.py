
### TO DO #####

# Basic object wiggle?
# handle inherit rotation?

# bugs:
# weird glitch when starting playback?

import bpy, math
from bpy.app.handlers import persistent
from mathutils import Vector, Matrix

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
    wo = bpy.context.scene.wiggle.list.get(ob.name)
    for wb in wo.list:
        reset_bone(bpy.data.objects.get(wo.name).pose.bones.get(wb.name))

def reset_bone(b):
    # 1. 시각적 포즈를 원래 자리로 리셋
    b.matrix_basis = Matrix.Identity(4)
    
    # 2. 강제로 본의 월드 행렬을 계산 (이 과정이 빠지면 위치값이 어긋납니다)
    b.id_data.update_tag()
    bpy.context.view_layer.update() 
    
    # 3. 업데이트된 본의 실제 월드 위치 확보
    world_mat = b.id_data.matrix_world
    current_world_matrix = world_mat @ b.matrix
    
    # 4. 물리 좌표를 현재 본의 '실제 월드 좌표'로 완벽 동기화
    # b.tail은 로컬 좌표이므로 월드 행렬을 곱해 정확한 위치를 얻어야 합니다.
    curr_tail_pos = (world_mat @ b.matrix @ Matrix.Translation(Vector((0, b.bone.length, 0)))).translation
    curr_head_pos = current_world_matrix.translation
    
    # 5. 가속도와 속도를 0으로 완전히 죽임 (날아가는 것 방지)
    b.wiggle.position = b.wiggle.position_last = curr_tail_pos
    b.wiggle.position_head = b.wiggle.position_last_head = curr_head_pos
    
    zero_v = Vector((0, 0, 0))
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
    # 1. 무한 루프 방지
    if context.scene.get("wiggle_updating"):
        return
    
    # 2. 속성값 미리 확보
    val = getattr(self, prop)
    context.scene["wiggle_updating"] = True
    
    try:
        if isinstance(self, bpy.types.PoseBone):
            selected_bones = context.selected_pose_bones
            
            # 3. 값 전파 루프 (최대한 가볍게)
            for b in selected_bones:
                if b != self:
                    # 속도 향상을 위한 직접 대입 시도
                    if prop in b:
                        b[prop] = val
                    else:
                        setattr(b, prop, val)

            # 4. 무거운 초기화 로직은 필요할 때만 실행
            if prop in ['wiggle_head', 'wiggle_tail']:
                # globals()에서 함수 존재 여부 확인 후 실행
                rb = globals().get('reset_bone')
                if rb:
                    for b in selected_bones:
                        rb(b)

        # 5. UI 및 리스트 갱신
        if prop in ['wiggle_mute', 'wiggle_enable', 'wiggle_head', 'wiggle_tail']:
            bl = globals().get('build_list')
            if bl:
                bl()

    finally:
        # 6. 플래그 해제
        context.scene["wiggle_updating"] = False
        
        # 7. 핵심 최적화: 불필요한 Redraw 방지
        ws = getattr(context.scene, "wiggle", None)
        if ws and hasattr(ws, "is_rendering"):
            if ws.is_rendering: # True일 때만 False로 바꿈
                ws.is_rendering = False


def get_parent(b):
    p = b.parent
    if not p: return None
    par = p if (p.wiggle_enable and (not p.wiggle_mute) and ((p.wiggle_head and not p.bone.use_connect) or p.wiggle_tail)) else get_parent(p)
    return par

def length_world(b):
    return (b.id_data.matrix_world @ b.head - b.id_data.matrix_world @ b.tail).length

def collider_poll(self, object):
    return object.type == 'MESH'

def wind_poll(self, object):
    return object.field and object.field.type =='WIND'

def collide(b,dg,head=False):
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
        if wiggle_collection in bpy.context.scene.collection.children_recursive:
            colliders = [ob for ob in wiggle_collection.objects if ob.type == 'MESH']
    col = False
    for collider in colliders:
        cmw = collider.matrix_world
        p = collider.closest_point_on_mesh(cmw.inverted() @ pos, depsgraph=dg)
        n = (cmw.to_quaternion().to_matrix().to_4x4() @ p[2]).normalized()
        i = cmw @ p[1]
        v = i-pos
        
        if (n.dot(v.normalized()) > 0.01) or (v.length < radius) or (co and (v.length < (radius+sticky))):
            if n.dot(v.normalized()) > 0: #vec is below
                nv = v.normalized()
            else: #normal is opposite dir to vec
                nv = -v.normalized()
            pos = i + nv*radius
            
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
            dist = (b.id_data.matrix_world @ par.matrix @ relative_matrix(par.matrix, b.matrix).translation - b.wiggle.position).length
            if p:
                dist = (p.wiggle.matrix @ relative_matrix(p.matrix, b.matrix).translation - b.wiggle.position).length
            sy = dist / l_world if l_world > 0.0001 else 1.0
        else:
            dist = (b.id_data.matrix_world @ b.matrix.translation - b.wiggle.position).length
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
        
        # 이전 계산된 오프셋을 가져옴 (없으면 현재 값으로 초기화)
        old_offset = guide_obj.get("last_offset", length / t)
        new_offset = length / t
        
        # 두께(스케일)를 먼저 업데이트
        guide_obj.scale = (t, t, t)
        
        is_capsule = len(guide_obj.data.vertices) > 100
        for v in guide_obj.data.vertices:
            if is_capsule:
                # [중요] 상단 반구 정점들만 골라서 위치 보정
                # 이전 오프셋을 빼서 원복(0.5 지점)시킨 뒤, 새 오프셋을 더함
                if v.co.z > 0.6: 
                    v.co.z = (v.co.z - old_offset) + new_offset
            else: # BOX, CYLINDER
                # 박스나 실린더는 천장 평면만 새 오프셋으로 고정
                if v.co.z > 0.1:
                    v.co.z = new_offset
        
        # 다음 업데이트를 위해 현재 오프셋 저장
        guide_obj["last_offset"] = new_offset
# END OF REVISION #

# START OF REVISION #

def move(b,dg):
    dt = bpy.context.scene.wiggle.dt
    dt2 = dt * dt
    if not dt: return

    if b.wiggle_tail:
        # 1. 물리 기본 연산 (초기 버전으로 복구)
        damp = max(min(1 - b.wiggle_damp * dt, 1), 0) 
        b.wiggle.velocity = b.wiggle.velocity * damp
        F = bpy.context.scene.gravity * b.wiggle_gravity
        
        # 바람(Wind) 로직 포함
        if b.wiggle_wind_ob:
            dir = b.wiggle_wind_ob.matrix_world.to_quaternion().to_matrix().to_4x4() @ Vector((0,0,1))
            F += dir * b.wiggle_wind_ob.field.strength * b.wiggle_wind / b.wiggle_mass
            
        b.wiggle.position += b.wiggle.velocity + F * dt2
        pin(b)
        
        # 2. 컬렉션 충돌 로직 (기능 유지)
        collider_obs = []
        if b.wiggle_collider_type == 'Object' and b.wiggle_collider:
            collider_obs.append(b.wiggle_collider)
        elif b.wiggle_collider_type == 'Collection' and b.wiggle_collider_collection:
            collider_obs = [o for o in b.wiggle_collider_collection.objects if o.type == 'MESH']

        for col_obj in collider_obs:
            orig_collider = b.wiggle_collider
            b.wiggle_collider = col_obj
            collide(b, dg)
            # 충돌 감지 시 속도만 살짝 줄여 안정화
            if b.wiggle.collision_normal.length > 0.001:
                b.wiggle.velocity *= 0.5
                break
            b.wiggle_collider = orig_collider
    
    # Head 부분 동일 복구
    if b.wiggle_head and not b.bone.use_connect:
        damp_h = max(min(1 - b.wiggle_damp_head * dt, 1), 0)
        b.wiggle.velocity_head = b.wiggle.velocity_head * damp_h
        F_h = bpy.context.scene.gravity * b.wiggle_gravity_head
        b.wiggle.position_head += b.wiggle.velocity_head + F_h * dt2
        
        colliders_h = [o for o in b.wiggle_collider_collection_head.objects if o.type == 'MESH'] if b.wiggle_collider_type_head == 'Collection' else ([b.wiggle_collider_head] if b.wiggle_collider_head else [])
        for col_obj in colliders_h:
            orig_h = b.wiggle_collider_head
            b.wiggle_collider_head = col_obj
            collide(b, dg, True)
            if b.wiggle.collision_normal_head.length > 0.001:
                b.wiggle.velocity_head *= 0.5
                break
            b.wiggle_collider_head = orig_h
            
    update_matrix(b)
    # 스케일 정규화 (5.0 필수)
    b.matrix = b.matrix.to_3x3().normalized().to_4x4()


# START OF REVISION #
def constrain(b, i, dg):
    dt = bpy.context.scene.wiggle.dt
    
    def get_fac(mass1, mass2):
        return 0.5 if mass1 == mass2 else mass1 / (mass1 + mass2)
    
    def spring(target, position, stiff):
        s = target - position
        Fs = s * stiff / bpy.context.scene.wiggle.iterations
        if (Fs * dt * dt).length > s.length:
            return s
        return Fs * dt * dt
    
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
def wiggle_pre(scene, depsgraph=None):
    if not scene.wiggle_enable: return
    if scene.get("wiggle_updating"): return
    for obj in scene.objects:
        if obj.type != 'ARMATURE' or not obj.wiggle_enable or obj.wiggle_mute: continue
        for pb in obj.pose.bones:
            if not pb.wiggle_enable or pb.wiggle_mute: continue
            if (pb.wiggle_head or pb.wiggle_tail) and not pb.wiggle_mute:
                pass

@persistent                
def wiggle_post(scene, dg):
    if (scene.wiggle.lastframe == scene.frame_current) and not scene.wiggle.reset: return
    if scene.wiggle.reset: return
    if not scene.wiggle_enable: return
    if scene.wiggle.is_rendering: return

    lastframe = scene.wiggle.lastframe
    curr_frame = scene.frame_current
    
    if (curr_frame <= scene.frame_start) or (curr_frame < lastframe):
        for wo in scene.wiggle.list:
            ob = scene.objects.get(wo.name)
            if not ob: continue
            for wb in wo.list:
                b = ob.pose.bones.get(wb.name)
                if not b: continue
                b.wiggle.position = b.wiggle.position_last = Vector((0,0,0))
                b.wiggle.position_head = b.wiggle.position_last_head = Vector((0,0,0))
                b.wiggle.velocity = b.wiggle.velocity_head = Vector((0,0,0))
        
        scene.wiggle.lastframe = curr_frame
        scene.wiggle.reset = False
        bpy.ops.wiggle.reset()
        return

    if curr_frame >= lastframe:
        frames_elapsed = curr_frame - lastframe
    else:
        e1 = (scene.frame_end - lastframe) + (curr_frame - scene.frame_start) + 1
        e2 = lastframe - curr_frame
        frames_elapsed = min(e1, e2)
        
    if frames_elapsed > 4: frames_elapsed = 1
    if scene.wiggle.is_preroll: frames_elapsed = 1
    
    scene.wiggle.dt = (1.0 / max(1.0, scene.render.fps)) * frames_elapsed
    scene.wiggle.lastframe = curr_frame

    for wo in scene.wiggle.list:
        ob = scene.objects.get(wo.name)
        if not ob or ob.wiggle_mute or ob.wiggle_freeze: continue
        
        bones = []
        for wb in wo.list:
            b = ob.pose.bones.get(wb.name)
            if not b or b.wiggle_mute or not (b.wiggle_head or b.wiggle_tail): continue
            bones.append(b)
            
        if not bones: continue

        for b in bones:
            b.wiggle.collision_normal = b.wiggle.collision_normal_head = Vector((0,0,0))
            move(b, dg)
            
        for i in range(max(1, scene.wiggle.iterations)):
            for b in bones:
                constrain(b, scene.wiggle.iterations - 1 - i, dg)
        
        for b in bones:
            update_matrix(b, True)
            
        if frames_elapsed:
            fe = max(frames_elapsed, 1)
            for b in bones:
                vb = Vector((0,0,0))
                if b.wiggle.collision_normal.length > 0.001:
                    vb = b.wiggle.velocity.reflect(b.wiggle.collision_normal).project(b.wiggle.collision_normal) * b.wiggle_bounce
                b.wiggle.velocity = (b.wiggle.position - b.wiggle.position_last) / fe + vb
                
                vb_h = Vector((0,0,0)) 
                if b.wiggle.collision_normal_head.length > 0.001:
                    vb_h = b.wiggle.velocity_head.reflect(b.wiggle.collision_normal_head).project(b.wiggle.collision_normal_head) * b.wiggle_bounce_head
                b.wiggle.velocity_head = (b.wiggle.position_head - b.wiggle.position_last_head) / fe + vb_h
                
                b.wiggle.position_last = b.wiggle.position.copy()
                b.wiggle.position_last_head = b.wiggle.position_head.copy()
        
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
def wiggle_load(scene):
    if 'build_list' in globals():
        build_list()
    scene.wiggle.is_rendering = False

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
def wiggle_load(scene):
    if 'build_list' in globals(): build_list()
    scene.wiggle.is_rendering = False
    scene.wiggle.lastframe = scene.frame_current
# END OF REVISION #

            
class WiggleCopy(bpy.types.Operator):
    """Copy active wiggle settings to selected bones"""
    bl_idname = "wiggle.copy"
    bl_label = "Copy Settings to Selected"
    
    @classmethod
    def poll(cls,context):
        return context.mode in ['POSE'] and context.active_pose_bone and (len(context.selected_pose_bones)>1)
    
    def execute(self,context):
        b = context.active_pose_bone
#        b.wiggle_enable = b.wiggle_enable
        b.wiggle_mute = b.wiggle_mute
        b.wiggle_head = b.wiggle_head
        b.wiggle_tail = b.wiggle_tail
        b.wiggle_head_mute = b.wiggle_head_mute
        b.wiggle_tail_mute = b.wiggle_tail_mute
        
        b.wiggle_mass = b.wiggle_mass
        b.wiggle_stiff = b.wiggle_stiff
        b.wiggle_stretch = b.wiggle_stretch
        b.wiggle_damp = b.wiggle_damp
        b.wiggle_gravity = b.wiggle_gravity
        b.wiggle_wind_ob = b.wiggle_wind_ob
        b.wiggle_wind = b.wiggle_wind
        b.wiggle_collider_type = b.wiggle_collider_type
        b.wiggle_collider = b.wiggle_collider
        b.wiggle_collider_collection = b.wiggle_collider_collection
        b.wiggle_radius = b.wiggle_radius
        b.wiggle_friction = b.wiggle_friction
        b.wiggle_bounce = b.wiggle_bounce
        b.wiggle_sticky = b.wiggle_sticky
        b.wiggle_chain = b.wiggle_chain
        
        b.wiggle_mass_head = b.wiggle_mass_head
        b.wiggle_stiff_head = b.wiggle_stiff_head
        b.wiggle_stretch_head = b.wiggle_stretch_head
        b.wiggle_damp_head = b.wiggle_damp_head
        b.wiggle_gravity_head = b.wiggle_gravity_head
        b.wiggle_wind_ob_head = b.wiggle_wind_ob_head
        b.wiggle_wind_head = b.wiggle_wind_head
        b.wiggle_collider_type_head = b.wiggle_collider_type_head
        b.wiggle_collider_head = b.wiggle_collider_head
        b.wiggle_collider_collection_head = b.wiggle_collider_collection_head
        b.wiggle_radius_head = b.wiggle_radius_head
        b.wiggle_friction_head = b.wiggle_friction_head
        b.wiggle_bounce_head = b.wiggle_bounce_head
        b.wiggle_sticky_head = b.wiggle_sticky_head
        b.wiggle_chain_head = b.wiggle_chain_head
        return {'FINISHED'}

# START OF REVISION #
class WiggleReset(bpy.types.Operator):
    bl_idname = "wiggle.reset"
    bl_label = "Reset Physics"
    
    @classmethod
    def poll(cls,context):
        return context.scene.wiggle_enable and context.mode in ['OBJECT', 'POSE']
    
    def execute(self, context):
        # 1. 물리 엔진 일시 정지 신호
        context.scene.wiggle.reset = True
        
        # 2. 리셋 실행 (위에서 수정한 reset_bone 호출)
        for wo in context.scene.wiggle.list:
            ob = context.scene.objects.get(wo.name)
            if ob:
                for wb in wo.list:
                    b = ob.pose.bones.get(wb.name)
                    if b: reset_bone(b)
        
        # 3. [핵심] 리셋된 수평 행렬값을 뷰 레이어에 즉시 반영
        context.view_layer.update()
        
        # 4. 마지막 연산 프레임을 현재로 고정하여 핸들러의 추적 방지
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
    
    @classmethod
    def poll(cls,context):
        return context.mode in ['POSE']
    
    def execute(self,context):
        bpy.ops.pose.select_all(action='DESELECT')
        rebuild = False
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
                # Blender 5.0 대응: 포즈 본과 데이터 본의 선택 상태를 모두 강제 적용
                b.select = True
                if hasattr(b.bone, "select"):
                    b.bone.select = True
                    
        if rebuild: build_list()
        return {'FINISHED'}
# END OF REVISION #

    
class WiggleBake(bpy.types.Operator):
    """Bake this object's visible wiggle bones to keyframes"""
    bl_idname = "wiggle.bake"
    bl_label = "Bake Wiggle"
    
    @classmethod
    def poll(cls,context):
        return context.object
    
    def execute(self,context):
        def push_nla():
            if context.scene.wiggle.bake_overwrite: return
            if not context.scene.wiggle.bake_nla: return
            if not context.object.animation_data: return
            if not context.object.animation_data.action: return
            action = context.object.animation_data.action
            track = context.object.animation_data.nla_tracks.new()
            track.name = action.name
            track.strips.new(action.name, int(action.frame_range[0]), action)
            
        push_nla()
        
        bpy.ops.wiggle.reset()
            
        #preroll
        duration = context.scene.frame_end - context.scene.frame_start
        preroll = context.scene.wiggle.preroll
        context.scene.wiggle.is_preroll = False
        bpy.ops.wiggle.select()
        bpy.ops.wiggle.reset()
        while preroll >= 0:
            if context.scene.wiggle.loop:
                frame = context.scene.frame_end - (preroll%duration)
                context.scene.frame_set(frame)
            else:
                context.scene.frame_set(context.scene.frame_start)
            context.scene.wiggle.is_preroll = True
            preroll -= 1
        #bake
        bpy.ops.nla.bake(frame_start = context.scene.frame_start,
                        frame_end = context.scene.frame_end,
                        only_selected = True,
                        visual_keying = True,
                        use_current_action = context.scene.wiggle.bake_overwrite,
                        bake_types={'POSE'})
        context.scene.wiggle.is_preroll = False
        context.object.wiggle_freeze = True
        if not context.scene.wiggle.bake_overwrite:
            context.object.animation_data.action.name = 'WiggleAction'
        return {'FINISHED'}  
# START OF REVISION #

class WiggleToggleBBox(bpy.types.Operator):
    bl_idname = "wiggle.toggle_bbox"
    bl_label = "Add/Remove Visual Guide"
    bl_options = {'REGISTER', 'UNDO'}

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
        selected_bones = context.selected_pose_bones
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
class WigglePreset(bpy.types.Operator):
    bl_idname = "wiggle.preset"
    bl_label = "Physics Preset"
    bl_options = {'REGISTER', 'UNDO'}
    type: bpy.props.StringProperty() 

    def execute(self, context):
        arm = context.object
        b = context.active_pose_bone
        if not b: return {'CANCELLED'}
        
        context.scene["wiggle_updating"] = True
        try:
            if self.type == 'JELLY': b.wiggle_stiff, b.wiggle_damp, b.wiggle_bounce, b.wiggle_gravity, b.wiggle_friction = 40.0, 0.2, 0.8, 0.3, 0.5
            elif self.type == 'HAIR': b.wiggle_stiff, b.wiggle_damp, b.wiggle_friction, b.wiggle_gravity, b.wiggle_bounce = 150.0, 0.5, 0.9, 1.0, 0.2
            elif self.type == 'HEAVY': b.wiggle_stiff, b.wiggle_damp, b.wiggle_gravity = 80.0, 0.4, 1.5
            elif self.type == 'CLOTH': b.wiggle_stiff, b.wiggle_damp, b.wiggle_gravity = 15.0, 0.7, 0.5
            elif self.type == 'SPRING': b.wiggle_stiff, b.wiggle_damp, b.wiggle_bounce = 400.0, 0.1, 1.0
            elif self.type == 'ANTENNA': b.wiggle_stiff, b.wiggle_damp, b.wiggle_bounce = 250.0, 0.05, 0.2
            
            bpy.ops.wiggle.reset() 
        finally:
            context.scene["wiggle_updating"] = False
            
        return {'FINISHED'}

class WiggleToggleBBox(bpy.types.Operator):
    bl_idname = "wiggle.toggle_bbox"
    bl_label = "Add/Remove Visual Guide"
    bl_options = {'REGISTER', 'UNDO'}

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
                # [캡슐 교정 핵심] 
                # Z > 0 (상단 반구): Tail 위치(offset)로 밀어줌
                # Z <= 0 (하단 반구): Head 위치(0)로 당겨줌 (0.5만큼 오프셋)
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
        selected_bones = context.selected_pose_bones
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
                
                # 페어런팅 및 기본 정렬 (X축 90도 회전 필수)
                g.parent, g.parent_type, g.parent_bone = arm, 'BONE', pb.name
                g.matrix_local = Matrix.Identity(4)
                g.rotation_euler = (1.570796, 0, 0)
                
                # 본 길이에 맞게 메시 변형 실행
                WiggleToggleBBox.update_mesh_shape(pb, context)

        # 활성 오브젝트 복구 및 모드 복귀
        context.view_layer.objects.active = arm
        if bpy.ops.object.mode_set.poll():
            try:
                bpy.ops.object.mode_set(mode=original_mode)
            except:
                bpy.ops.object.mode_set(mode='OBJECT')
            
        return {'FINISHED'}


class WiggleReset(bpy.types.Operator):
    bl_idname = "wiggle.reset"
    bl_label = "Hard Reset Physics & Cache"
    bl_options = {'REGISTER', 'UNDO'}
    def execute(self, context):
        arm = context.object
        if not arm or arm.type != 'ARMATURE': return {'CANCELLED'}
        context.scene["wiggle_updating"] = True
        try:
            if 'reset_ob' in globals(): reset_ob(arm)
            context.view_layer.update()
            context.scene.frame_set(context.scene.frame_current)
            self.report({'INFO'}, "Wiggle: System Reset Complete.")
        finally: context.scene["wiggle_updating"] = False
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
        name = 'Stiff', min = 0, default = 400, override={'LIBRARY_OVERRIDABLE'},
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
        name = 'Stiff', min = 0, default = 400, override={'LIBRARY_OVERRIDABLE'},
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
        name='Collider Type', items=[('Object','Object',''),('Collection','Collection','')],
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
        name='Collider Type', items=[('Object','Object',''),('Collection','Collection','')],
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
        WiggleObject, WiggleScene, WiggleReset, WiggleCopy, WiggleSelect, WiggleBake
    )
    for cls in classes:
        if not hasattr(bpy.types, cls.__name__): bpy.utils.register_class(cls)

    bpy.types.PoseBone.wiggle = bpy.props.PointerProperty(type=WiggleBone, override={'LIBRARY_OVERRIDABLE'})
    bpy.types.Object.wiggle = bpy.props.PointerProperty(type=WiggleObject, override={'LIBRARY_OVERRIDABLE'})
    bpy.types.Scene.wiggle = bpy.props.PointerProperty(type=WiggleScene, override={'LIBRARY_OVERRIDABLE'})
    
    h_pre = bpy.app.handlers.frame_change_pre
    if wiggle_pre not in h_pre: h_pre.append(wiggle_pre)
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

def unregister():
    classes = (
        WiggleToggleBBox, WigglePreset, WiggleBoneItem, WiggleItem, WiggleBone,
        WiggleObject, WiggleScene, WiggleReset, WiggleCopy, WiggleSelect, WiggleBake
    )
    for cls in reversed(classes):
        if hasattr(bpy.types, cls.__name__): bpy.utils.unregister_class(cls)
    
    if wiggle_pre in bpy.app.handlers.frame_change_pre: bpy.app.handlers.frame_change_pre.remove(wiggle_pre)
    if wiggle_post in bpy.app.handlers.frame_change_post: bpy.app.handlers.frame_change_post.remove(wiggle_post)
    if wiggle_render_pre in bpy.app.handlers.render_pre: bpy.app.handlers.render_pre.remove(wiggle_render_pre)
    if wiggle_render_post in bpy.app.handlers.render_post: bpy.app.handlers.render_post.remove(wiggle_render_post)
    if wiggle_render_cancel in bpy.app.handlers.render_cancel: bpy.app.handlers.render_cancel.remove(wiggle_render_cancel)
    if wiggle_load in bpy.app.handlers.load_post: bpy.app.handlers.load_post.remove(wiggle_load)
