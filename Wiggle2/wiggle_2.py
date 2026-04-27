
### TO DO #####

# Basic object wiggle?
# handle inherit rotation?

# bugs:
# weird glitch when starting playback?

import bpy, math
from mathutils import Vector, Matrix, Euler, Quaternion, geometry
from bpy.app.handlers import persistent

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
            # 5.0 대응: 스케일 오염 방지를 위해 강제 1.0 정규화 후 decompose 참조
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
    
    # --- 스케일(sy) 계산 및 찌그러짐 방지 클램프 ---
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
            
    # 5.0 핵심 수정: stretch 값이 작을 때 본이 늘어나거나 찌그러지는 것 방지
    # 사용자가 Stretch 값을 높게 주지 않았다면 sy를 1.0에 가깝게 강제 고정
    if not (b.wiggle_head or b.wiggle_tail) or (hasattr(b, 'wiggle_stretch') and b.wiggle_stretch < 0.01):
        sy = max(0.999, min(1.001, sy))

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
            
    # 최종 행렬 정규화 (스케일 누적 방지)
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
# can include gravity, wind, etc    
def move(b,dg):
    dt = bpy.context.scene.wiggle.dt
    dt2 = dt * dt
    if dt:
        if b.wiggle_tail:
            damp = max(min(1-b.wiggle_damp*dt, 1),0) 
            b.wiggle.velocity=b.wiggle.velocity*damp
            F = bpy.context.scene.gravity * b.wiggle_gravity
            if b.wiggle_wind_ob:
                # 5.0 대응: 행렬에서 순수 회전만 추출하여 풍향 계산 (스케일 간섭 방지)
                dir = b.wiggle_wind_ob.matrix_world.to_quaternion().to_matrix().to_4x4() @ Vector((0,0,1))
                v_dist = (b.wiggle.position - b.wiggle.matrix.translation)
                v_dist_len = v_dist.length
                fac = 1.0
                if v_dist_len > 0.0001:
                    fac = 1 - b.wiggle_wind_ob.field.wind_factor * abs(dir.dot(v_dist.normalized()))
                F += dir * fac * b.wiggle_wind_ob.field.strength * b.wiggle_wind / b.wiggle_mass
            b.wiggle.position += b.wiggle.velocity + F*dt2
            pin(b)
            collide(b,dg)
        
        if b.wiggle_head and not b.bone.use_connect:
            damp = max(min(1-b.wiggle_damp_head*dt,1),0)
            b.wiggle.velocity_head = b.wiggle.velocity_head*damp
            F = bpy.context.scene.gravity * b.wiggle_gravity_head
            if b.wiggle_wind_ob_head:
                dir = b.wiggle_wind_ob_head.matrix_world.to_quaternion().to_matrix().to_4x4() @ Vector((0,0,1))
                F += dir * b.wiggle_wind_ob_head.field.strength * b.wiggle_wind_head / b.wiggle_mass_head
            b.wiggle.position_head += b.wiggle.velocity_head + F*dt2
            collide(b,dg,True)
            
        update_matrix(b)
        # 5.0 대응: 최종 연산 후 본의 행렬에서 스케일 발산 강제 차단 (정규화)
        b.matrix = b.matrix.to_3x3().normalized().to_4x4()
        b.matrix.translation = b.matrix.translation
# END OF REVISION #


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
def wiggle_pre(scene):
    if (scene.wiggle.lastframe == scene.frame_current) and not scene.wiggle.reset: return
    if scene.wiggle.is_rendering: return
    if not scene.wiggle_enable:
        reset_scene()
        return
    for wo in scene.wiggle.list:
        if wo.name not in scene.objects:
            build_list()
            return
        ob = scene.objects[wo.name]
        if ob.wiggle_mute or ob.wiggle_freeze:
            reset_ob(ob)
            continue
        for wb in wo.list:
            if wb.name not in ob.pose.bones:
                build_list()
                return
            b = ob.pose.bones[wb.name]
            if b.wiggle_mute or not (b.wiggle_head or b.wiggle_tail):
                reset_bone(b)
                continue
            if not b.wiggle.collision_col:
                if b.wiggle_collider_collection:
                    b.wiggle_collider_collection = bpy.data.collections.get(b.wiggle_collider_collection.name)
                    b.wiggle.collision_col = scene.collection
                elif b.wiggle_collider_collection_head:
                    bpy.data.collections.get(b.wiggle_collider_collection_head.name)
                    b.wiggle.collision_col = scene.collection
                elif b.wiggle_collider:
                    bpy.data.objects.get(b.wiggle_collider.name)
                    b.wiggle.collision_col = scene.collection
                elif b.wiggle_collider_head:
                    bpy.data.objects.get(b.wiggle_collider_head.name)
                    b.wiggle.collision_col = scene.collection
            b.location = Vector((0,0,0))
            b.rotation_quaternion = Quaternion((1,0,0,0))
            b.rotation_euler = Vector((0,0,0))
            b.scale = Vector((1,1,1))
    bpy.context.view_layer.update()

# START OF REVISION #
@persistent                
def wiggle_post(scene, dg):
    # 1. 실행 조건 체크
    if scene.wiggle.is_rendering: return
    if not scene.wiggle_enable: return

    curr_frame = scene.frame_current
    lastframe = scene.wiggle.lastframe

    # 2. [핵심 수정] 시작 프레임이나 0프레임으로 돌아갔을 때 강제 리셋
    # 타임라인을 점프해서 앞으로 갔을 때 데이터를 초기화해야 꼬이지 않습니다.
    if curr_frame <= scene.frame_start or scene.wiggle.reset:
        reset_scene()
        scene.wiggle.lastframe = curr_frame
        scene.wiggle.reset = False
        return

    # 프레임 중복 계산 방지
    if curr_frame == lastframe: return

    # 3. 프레임 경과 계산
    if curr_frame > lastframe:
        frames_elapsed = curr_frame - lastframe
    else:
        # 역재생이나 루프 시 1프레임으로 간주하여 리셋 방지
        frames_elapsed = 1
        
    if frames_elapsed > 4: frames_elapsed = 1
    if scene.wiggle.is_preroll: frames_elapsed = 1
    
    scene.wiggle.dt = (1.0 / max(1.0, scene.render.fps)) * frames_elapsed
    scene.wiggle.lastframe = curr_frame

    # 4. 시뮬레이션 및 데이터 업데이트
    for wo in scene.wiggle.list:
        ob = scene.objects.get(wo.name)
        if not ob or ob.wiggle_mute or ob.wiggle_freeze: continue
        
        bones = [ob.pose.bones[wb.name] for wb in wo.list 
                 if wb.name in ob.pose.bones and not ob.pose.bones[wb.name].wiggle_mute]
            
        if not bones: continue

        for b in bones:
            b.wiggle.collision_normal = b.wiggle.collision_normal_head = Vector((0,0,0))
            move(b, dg)
            
        for i in range(max(1, scene.wiggle.iterations)):
            for b in bones:
                constrain(b, scene.wiggle.iterations - 1 - i, dg)
        
        for b in bones:
            # last=True로 설정하여 시각적 포즈 업데이트
            update_matrix(b, True)
            
            if frames_elapsed:
                # 속도 및 이전 위치 저장 (.copy() 필수)
                b.wiggle.velocity = (b.wiggle.position - b.wiggle.position_last) / frames_elapsed
                b.wiggle.velocity_head = (b.wiggle.position_head - b.wiggle.position_last_head) / frames_elapsed
                b.wiggle.position_last = b.wiggle.position.copy()
                b.wiggle.position_last_head = b.wiggle.position_head.copy()
        
        # 5. 캐시 기록 활성화 (타임라인 빨간 줄 생성)
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
class WigglePanel:
    bl_category = 'Animation'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    
    @classmethod
    def poll(cls, context):
        return context.object is not None

class WIGGLE_PT_Settings(WigglePanel, bpy.types.Panel):
    bl_label = 'Wiggle 2'
        
    def draw(self, context):
        layout = self.layout
        scene = context.scene
        obj = context.object

        row = layout.row()
        icon = 'HIDE_ON' if not scene.wiggle_enable else 'SCENE_DATA'
        row.prop(scene, "wiggle_enable", icon=icon, text="", emboss=False)
        
        if not scene.wiggle_enable:
            row.label(text='Scene muted.')
            return
            
        if not obj or obj.type != 'ARMATURE':
            row.label(text=' Select armature.')
            return

        if obj.wiggle_freeze:
            row.prop(obj, 'wiggle_freeze', icon='FREEZE', icon_only=True, emboss=False)
            row.label(text='Wiggle Frozen after Bake.')
            # 여기서 return하지 않고 다음 섹션을 그릴 수 있게 통과시킴
        else:
            icon = 'HIDE_ON' if obj.wiggle_mute else 'ARMATURE_DATA'
            row.prop(obj, 'wiggle_mute', icon=icon, icon_only=True, invert_checkbox=True, emboss=False)
            
            if obj.wiggle_mute:
                row.label(text='Armature muted.')
                # return을 제거하여 하단 유틸리티 메뉴가 잘리지 않게 함
            else:
                pb = context.active_pose_bone
                if not pb:
                    row.label(text=' Select pose bone.')
                else:
                    icon = 'HIDE_ON' if pb.wiggle_mute else 'BONE_DATA'
                    row.prop(pb, 'wiggle_mute', icon=icon, icon_only=True, invert_checkbox=True, emboss=False)
                    if pb.wiggle_mute:
                        row.label(text='Bone muted.')
# END OF REVISION #


class WIGGLE_PT_Head(WigglePanel,bpy.types.Panel):
    bl_label = ''
    bl_parent_id = 'WIGGLE_PT_Settings'
    bl_options = {'HEADER_LAYOUT_EXPAND'}
    
    @classmethod
    def poll(cls,context):
#        return context.active_pose_bone and not context.active_pose_bone.bone.use_connect
        return context.scene.wiggle_enable and context.object and not context.object.wiggle_mute and context.active_pose_bone and not context.active_pose_bone.wiggle_mute and not context.active_pose_bone.bone.use_connect
    
    def draw_header(self,context):
        row=self.layout.row(align=True)
        row.prop(context.active_pose_bone, 'wiggle_head')
    
    def draw(self,context):
        b = context.active_pose_bone
        if not b.wiggle_head: return
    
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        
        def drawprops(layout,b,props):
            for p in props:
                layout.prop(b, p)
        
        col = layout.column(align=True)
        drawprops(col,b,['wiggle_mass_head','wiggle_stiff_head','wiggle_stretch_head','wiggle_damp_head'])
        col.separator()
        col.prop(b,'wiggle_gravity_head')
        row=col.row(align=True)
        row.prop(b,'wiggle_wind_ob_head')
        sub = row.row(align=True)
        sub.ui_units_x = 5
        sub.prop(b, 'wiggle_wind_head', text='')
        col.separator()
        col.prop(b, 'wiggle_collider_type_head',text='Collisions')
        collision = False
        if b.wiggle_collider_type_head == 'Object':
            row = col.row(align=True)
            row.prop_search(b, 'wiggle_collider_head', context.scene, 'objects',text=' ')
            if b.wiggle_collider_head:
                if b.wiggle_collider_head.name in context.scene.objects:
                    collision = True
                else:
                    row.label(text='',icon='UNLINKED')
        else:
            row = col.row(align=True)
            row.prop_search(b, 'wiggle_collider_collection_head', bpy.data, 'collections', text=' ')
            if b.wiggle_collider_collection_head:
                if b.wiggle_collider_collection_head in context.scene.collection.children_recursive:
                    collision = True
                else:
                    row.label(text='',icon='UNLINKED')
            
        if collision:
            col = layout.column(align=True)
            drawprops(col,b,['wiggle_radius_head','wiggle_friction_head','wiggle_bounce_head','wiggle_sticky_head'])
        layout.prop(b,'wiggle_chain_head')
            
class WIGGLE_PT_Tail(WigglePanel,bpy.types.Panel):
    bl_label = ''
    bl_parent_id = 'WIGGLE_PT_Settings'
    bl_options = {'HEADER_LAYOUT_EXPAND'}
    
    @classmethod
    def poll(cls,context):
#        return context.active_pose_bone
        return context.scene.wiggle_enable and context.object and not context.object.wiggle_mute and context.active_pose_bone and not context.active_pose_bone.wiggle_mute
    
    def draw_header(self,context):
        row=self.layout.row(align=True)
        row.prop(context.active_pose_bone, 'wiggle_tail')
        
    def draw(self,context):
        b = context.active_pose_bone
        if not b.wiggle_tail: return
    
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        
        def drawprops(layout,b,props):
            for p in props:
                layout.prop(b, p)
        
        col = layout.column(align=True)
        drawprops(col,b,['wiggle_mass','wiggle_stiff','wiggle_stretch','wiggle_damp'])
        col.separator()
        col.prop(b,'wiggle_gravity')
        row=col.row(align=True)
        row.prop(b,'wiggle_wind_ob')
        sub = row.row(align=True)
        sub.ui_units_x = 5
        sub.prop(b, 'wiggle_wind', text='')
        col.separator()
        col.prop(b, 'wiggle_collider_type',text='Collisions')
        collision = False
        if b.wiggle_collider_type == 'Object':
            row = col.row(align=True)
            row.prop_search(b, 'wiggle_collider', context.scene, 'objects',text=' ')
            if b.wiggle_collider:
                if b.wiggle_collider.name in context.scene.objects:
                    collision = True
                else:
                    row.label(text='',icon='UNLINKED')
        else:
            row = col.row(align=True)
            row.prop_search(b, 'wiggle_collider_collection', bpy.data, 'collections', text=' ')
            if b.wiggle_collider_collection:
                if b.wiggle_collider_collection in context.scene.collection.children_recursive:
                    collision = True
                else:
                    row.label(text='',icon='UNLINKED')
        if collision:
            col = layout.column(align=True)
            drawprops(col,b,['wiggle_radius','wiggle_friction','wiggle_bounce','wiggle_sticky'])
        layout.prop(b,'wiggle_chain')

class WIGGLE_PT_Utilities(WigglePanel,bpy.types.Panel):
    bl_label = 'Global Wiggle Utilities'
    bl_parent_id = 'WIGGLE_PT_Settings'
    bl_options = {"DEFAULT_CLOSED"}
    
    @classmethod
    def poll(cls,context):
        return context.scene.wiggle_enable
    
    def draw(self,context):
        layout = self.layout
        layout.use_property_split=True
        layout.use_property_decorate=False
        col = layout.column(align=True)
        if context.object.wiggle_enable and context.mode == 'POSE':
            col.operator('wiggle.copy')
            col.operator('wiggle.select')
        col.operator('wiggle.reset')
        layout.prop(context.scene.wiggle, 'loop')
        layout.prop(context.scene.wiggle, 'iterations')
        
class WIGGLE_PT_Bake(WigglePanel,bpy.types.Panel):
    bl_label = 'Bake Wiggle'
    bl_parent_id = 'WIGGLE_PT_Utilities'
    bl_options = {"DEFAULT_CLOSED"}
    
    @classmethod
    def poll(cls,context):
        return context.scene.wiggle_enable and context.object.wiggle_enable and context.mode == 'POSE'
    
    def draw(self,context):
        layout = self.layout
        layout.use_property_split=True
        layout.use_property_decorate=False
        layout.prop(context.scene.wiggle, 'preroll')
        layout.prop(context.scene.wiggle, 'bake_overwrite')
        row = layout.row()
        row.enabled = not context.scene.wiggle.bake_overwrite
        row.prop(context.scene.wiggle, 'bake_nla')
        layout.operator('wiggle.bake')
        
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
    
    #WIGGLE TOGGLES
    
    bpy.types.Scene.wiggle_enable = bpy.props.BoolProperty(
        name = 'Enable Scene',
        description = 'Enable wiggle on this scene',
        default = False,
        override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_enable')
    )
    bpy.types.Object.wiggle_enable = bpy.props.BoolProperty(
        name = 'Enable Armature',
        description = 'Enable wiggle on this armature',
        default = False,
        options={'HIDDEN'},
        override={'LIBRARY_OVERRIDABLE'}
#        update=lambda s, c: update_prop(s, c, 'wiggle_enable')
    )
    bpy.types.Object.wiggle_mute = bpy.props.BoolProperty(
        name = 'Mute Armature',
        description = 'Mute wiggle on this armature.',
        default = False,
        override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_mute')
    )
    bpy.types.Object.wiggle_freeze = bpy.props.BoolProperty(
        name = 'Freeze Wiggle',
        description = 'Wiggle Calculation frozen after baking',
        default = False,
        override={'LIBRARY_OVERRIDABLE'}
    )
    bpy.types.PoseBone.wiggle_enable = bpy.props.BoolProperty(
        name = 'Enable Bone',
        description = "Enable wiggle on this bone",
        default = False,
        options={'HIDDEN'},
        override={'LIBRARY_OVERRIDABLE'}
#        update=lambda s, c: update_prop(s, c, 'wiggle_enable')
    )
    bpy.types.PoseBone.wiggle_mute = bpy.props.BoolProperty(
        name = 'Mute Bone',
        description = "Mute wiggle for this bone.",
        default = False,
        override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_mute')
    )
    bpy.types.PoseBone.wiggle_head = bpy.props.BoolProperty(
        name = 'Bone Head',
        description = "Enable wiggle on this bone's head",
        default = False,
        override={'LIBRARY_OVERRIDABLE'},
        options={'HIDDEN'},
        update=lambda s, c: update_prop(s, c, 'wiggle_head')
    )
    bpy.types.PoseBone.wiggle_tail = bpy.props.BoolProperty(
        name = 'Bone Tail',
        description = "Enable wiggle on this bone's tail",
        default = False,
        override={'LIBRARY_OVERRIDABLE'},
        options={'HIDDEN'},
        update=lambda s, c: update_prop(s, c, 'wiggle_tail')
    )
    
    bpy.types.PoseBone.wiggle_head_mute = bpy.props.BoolProperty(
        name = 'Bone Head Mute',
        description = "Mute wiggle on this bone's head",
        default = False,
        override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_head_mute')
    )
    bpy.types.PoseBone.wiggle_tail_mute = bpy.props.BoolProperty(
        name = 'Bone Tail Mute',
        description = "Mute wiggle on this bone's tail",
        default = False,
        override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_tail_mute')
    )
    
    #TAIL PROPS
    
    bpy.types.PoseBone.wiggle_mass = bpy.props.FloatProperty(
        name = 'Mass',
        description = 'Mass of bone',
        min = 0.01,
        default = 1,
        override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_mass')
    )
    bpy.types.PoseBone.wiggle_stiff = bpy.props.FloatProperty(
        name = 'Stiff',
        description = 'Spring stiffness coefficient, can be large numbers',
        min = 0,
        default = 400,
        override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_stiff')
    )
    bpy.types.PoseBone.wiggle_stretch = bpy.props.FloatProperty(
        name = 'Stretch',
        description = 'Bone stretchiness factor, 0 to 1 range',
        min = 0,
        default = 0,
        max=1,
        override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_stretch')
    )
    bpy.types.PoseBone.wiggle_damp = bpy.props.FloatProperty(
        name = 'Damp',
        description = 'Dampening coefficient, can be greater than 1',
        min = 0,
        default = 1,
        override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_damp')
    )
    bpy.types.PoseBone.wiggle_gravity = bpy.props.FloatProperty(
        name = 'Gravity',
        description = 'Multiplier for scene gravity',
        default = 1,
        override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_gravity')
    )
    bpy.types.PoseBone.wiggle_wind_ob = bpy.props.PointerProperty(
        name='Wind', 
        description='Wind force field object', 
        type=bpy.types.Object, 
        poll = wind_poll, 
        override={'LIBRARY_OVERRIDABLE'}, 
        update=lambda s, c: update_prop(s, c, 'wiggle_wind_ob')
    )
    bpy.types.PoseBone.wiggle_wind = bpy.props.FloatProperty(
        name = 'Wind Multiplier',
        description = 'Multiplier for wind forces',
        default = 1,
        override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_wind')
    )
    bpy.types.PoseBone.wiggle_chain = bpy.props.BoolProperty(
        name = 'Chain',
        description = 'Bone affects its parent creating a physics chain',
        default = True,
        override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_chain')
    )
    
    #HEAD PROPS
    
    bpy.types.PoseBone.wiggle_mass_head = bpy.props.FloatProperty(
        name = 'Mass',
        description = 'Mass of bone',
        min = 0.01,
        default = 1,
        override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_mass_head')
    )
    bpy.types.PoseBone.wiggle_stiff_head = bpy.props.FloatProperty(
        name = 'Stiff',
        description = 'Spring stiffness coefficient, can be large numbers',
        min = 0,
        default = 400,
        override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_stiff_head')
    )
    bpy.types.PoseBone.wiggle_stretch_head = bpy.props.FloatProperty(
        name = 'Stretch',
        description = 'Bone stretchiness factor, 0 to 1 range',
        min = 0,
        default = 0,
        max=1,
        override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_stretch_head')
    )
    bpy.types.PoseBone.wiggle_damp_head = bpy.props.FloatProperty(
        name = 'Damp',
        description = 'Dampening coefficient, can be greater than 1',
        min = 0,
        default = 1,
        override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_damp_head')
    )
    bpy.types.PoseBone.wiggle_gravity_head = bpy.props.FloatProperty(
        name = 'Gravity',
        description = 'Multiplier for scene gravity',
        default = 1,
        override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_gravity_head')
    )
    bpy.types.PoseBone.wiggle_wind_ob_head = bpy.props.PointerProperty(
        name='Wind', 
        description='Wind force field object', 
        type=bpy.types.Object, 
        poll = wind_poll, 
        override={'LIBRARY_OVERRIDABLE'}, 
        update=lambda s, c: update_prop(s, c, 'wiggle_wind_ob_head')
    )
    bpy.types.PoseBone.wiggle_wind_head = bpy.props.FloatProperty(
        name = 'Wind',
        description = 'Multiplier for wind forces',
        default = 1,
        override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_wind_head')
    )
    bpy.types.PoseBone.wiggle_chain_head = bpy.props.BoolProperty(
        name = 'Chain',
        description = 'Bone affects its parent creating a physics chain',
        default = True,
        override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_chain_head')
    )
    
    #TAIL COLLISION
    
    bpy.types.PoseBone.wiggle_collider_type = bpy.props.EnumProperty(
        name='Collider Type',
        items=[('Object','Object','Collide with a selected mesh'),('Collection','Collection','Collide with all meshes in selected collection')],
        override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_collider_type')
    )
    bpy.types.PoseBone.wiggle_collider = bpy.props.PointerProperty(
        name='Collider Object', 
        description='Mesh object to collide with', 
        type=bpy.types.Object, 
        poll = collider_poll, 
        override={'LIBRARY_OVERRIDABLE'}, 
        update=lambda s, c: update_prop(s, c, 'wiggle_collider')
    )
    bpy.types.PoseBone.wiggle_collider_collection = bpy.props.PointerProperty(
        name = 'Collider Collection', 
        description='Collection to collide with', 
        type=bpy.types.Collection, 
        override={'LIBRARY_OVERRIDABLE'}, 
        update=lambda s, c: update_prop(s, c, 'wiggle_collider_collection')
    )
    
    bpy.types.PoseBone.wiggle_radius = bpy.props.FloatProperty(
        name = 'Radius',
        description = 'Collision radius',
        min = 0,
        default = 0,
        override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_radius')
    )
    bpy.types.PoseBone.wiggle_friction = bpy.props.FloatProperty(
        name = 'Friction',
        description = 'Friction when colliding',
        min = 0,
        default = 0.5,
        soft_max = 1,
        override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_friction')
    )
    bpy.types.PoseBone.wiggle_bounce = bpy.props.FloatProperty(
        name = 'Bounce',
        description = 'Bounciness when colliding',
        min = 0,
        default = 0.5,
        soft_max = 1,
        override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_bounce')
    )
    bpy.types.PoseBone.wiggle_sticky = bpy.props.FloatProperty(
        name = 'Sticky',
        description = 'Margin beyond radius to keep item stuck to surface',
        min = 0,
        default = 0,
        soft_max = 1,
        override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_sticky')
    )
    
    #HEAD COLLISION
    
    bpy.types.PoseBone.wiggle_collider_type_head = bpy.props.EnumProperty(
        name='Collider Type',
        items=[('Object','Object','Collide with a selected mesh'),('Collection','Collection','Collide with all meshes in selected collection')],
        override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_collider_type_head')
    )
    bpy.types.PoseBone.wiggle_collider_head = bpy.props.PointerProperty(
        name='Collider Object', 
        description='Mesh object to collide with', 
        type=bpy.types.Object, 
        poll = collider_poll, 
        override={'LIBRARY_OVERRIDABLE'}, 
        update=lambda s, c: update_prop(s, c, 'wiggle_collider_head')
    )
    bpy.types.PoseBone.wiggle_collider_collection_head = bpy.props.PointerProperty(
        name = 'Collider Collection', 
        description='Collection to collide with', 
        type=bpy.types.Collection, 
        override={'LIBRARY_OVERRIDABLE'}, 
        update=lambda s, c: update_prop(s, c, 'wiggle_collider_collection_head')
    )
    
    bpy.types.PoseBone.wiggle_radius_head = bpy.props.FloatProperty(
        name = 'Radius',
        description = 'Collision radius',
        min = 0,
        default = 0,
        override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_radius_head')
    )
    bpy.types.PoseBone.wiggle_friction_head = bpy.props.FloatProperty(
        name = 'Friction',
        description = 'Friction when colliding',
        min = 0,
        default = 0.5,
        soft_max = 1,
        override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_friction_head')
    )
    bpy.types.PoseBone.wiggle_bounce_head = bpy.props.FloatProperty(
        name = 'Bounce',
        description = 'Bounciness when colliding',
        min = 0,
        default = 0.5,
        soft_max = 1,
        override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_bounce_head')
    )
    bpy.types.PoseBone.wiggle_sticky_head = bpy.props.FloatProperty(
        name = 'Sticky',
        description = 'Margin beyond radius to keep item stuck to surface',
        min = 0,
        default = 0,
        soft_max = 1,
        override={'LIBRARY_OVERRIDABLE'},
        update=lambda s, c: update_prop(s, c, 'wiggle_sticky_head')
    )
    
    #internal variables
    bpy.utils.register_class(WiggleBoneItem)
    bpy.utils.register_class(WiggleItem)
    bpy.utils.register_class(WiggleBone)
    bpy.types.PoseBone.wiggle = bpy.props.PointerProperty(type=WiggleBone, override={'LIBRARY_OVERRIDABLE'})
    bpy.utils.register_class(WiggleObject)
    bpy.types.Object.wiggle = bpy.props.PointerProperty(type=WiggleObject, override={'LIBRARY_OVERRIDABLE'})
    bpy.utils.register_class(WiggleScene)
    bpy.types.Scene.wiggle = bpy.props.PointerProperty(type=WiggleScene, override={'LIBRARY_OVERRIDABLE'})
    
    bpy.utils.register_class(WiggleReset)
    bpy.utils.register_class(WiggleCopy)
    bpy.utils.register_class(WiggleSelect)
    bpy.utils.register_class(WiggleBake)
    bpy.utils.register_class(WIGGLE_PT_Settings)
    bpy.utils.register_class(WIGGLE_PT_Head)
    bpy.utils.register_class(WIGGLE_PT_Tail)
    bpy.utils.register_class(WIGGLE_PT_Utilities)
    bpy.utils.register_class(WIGGLE_PT_Bake)
    
#    bpy.app.handlers.frame_change_pre.clear()
#    bpy.app.handlers.frame_change_post.clear()
#    bpy.app.handlers.render_pre.clear()
#    bpy.app.handlers.render_post.clear()
#    bpy.app.handlers.render_cancel.clear()
#    bpy.app.handlers.load_post.clear()
    
    bpy.app.handlers.frame_change_pre.append(wiggle_pre)
    bpy.app.handlers.frame_change_post.append(wiggle_post)
    bpy.app.handlers.render_pre.append(wiggle_render_pre)
    bpy.app.handlers.render_post.append(wiggle_render_post)
    bpy.app.handlers.render_cancel.append(wiggle_render_cancel)
    bpy.app.handlers.load_post.append(wiggle_load)

def unregister():
    bpy.utils.unregister_class(WiggleBoneItem)
    bpy.utils.unregister_class(WiggleItem)
    bpy.utils.unregister_class(WiggleBone)
    bpy.utils.unregister_class(WiggleObject)
    bpy.utils.unregister_class(WiggleScene)
    bpy.utils.unregister_class(WiggleReset)
    bpy.utils.unregister_class(WiggleCopy)
    bpy.utils.unregister_class(WiggleSelect)
    bpy.utils.unregister_class(WiggleBake)
    bpy.utils.unregister_class(WIGGLE_PT_Settings)
    bpy.utils.unregister_class(WIGGLE_PT_Head)
    bpy.utils.unregister_class(WIGGLE_PT_Tail)
    bpy.utils.unregister_class(WIGGLE_PT_Utilities)
    bpy.utils.unregister_class(WIGGLE_PT_Bake)
    
    bpy.app.handlers.frame_change_pre.remove(wiggle_pre)
    bpy.app.handlers.frame_change_post.remove(wiggle_post)
    bpy.app.handlers.render_pre.remove(wiggle_render_pre)
    bpy.app.handlers.render_post.remove(wiggle_render_post)
    bpy.app.handlers.render_cancel.remove(wiggle_render_cancel)
    bpy.app.handlers.load_post.remove(wiggle_load)
    
if __name__ == "__main__":
    register()
