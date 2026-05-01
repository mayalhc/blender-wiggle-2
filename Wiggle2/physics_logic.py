import bpy

def get_bone_chain(active_bone):
    """활성화된 본으로부터 루트를 찾아 전체 체인 리스트를 반환"""
    if not active_bone: return []
    root = active_bone
    while root.parent and isinstance(root.parent, bpy.types.PoseBone):
        root = root.parent
    chain = []
    curr = root
    while curr:
        chain.append(curr)
        # 튜플 에러 방지를 위해 인덱스 [0] 사용
        if curr.children and len(curr.children) > 0:
            curr = curr.children[0] 
        else:
            curr = None 
    return chain

def apply_taper_to_chain(context, target_attr, start_val, end_val):
    """
    [중요] 프리셋 버튼 등에서 즉시 값을 주입하기 위해 사용하는 함수
    target_attr: 'wiggle_stiff' 또는 'wiggle_damp'
    """
    active_bone = context.active_pose_bone
    if not active_bone: return
    
    chain = get_bone_chain(active_bone)
    count = len(chain)
    if count < 2: return

    for i, bone in enumerate(chain):
        factor = i / (count - 1)
        val = start_val + (end_val - start_val) * factor
        if hasattr(bone, target_attr):
            setattr(bone, target_attr, val)

def apply_values_safely():
    """타이머에 의해 호출되어 슬라이더 조작 시 안전하게 값을 주입"""
    context = bpy.context
    active_bone = context.active_pose_bone
    if not active_bone: return None

    scene = context.scene
    # 위에서 만든 함수를 재활용하여 Stiff/Damp 적용
    if getattr(active_bone, "wiggle_stiff_use_dist", False):
        apply_taper_to_chain(context, "wiggle_stiff", scene.wiggle_stiff_start, scene.wiggle_stiff_end)

    if getattr(active_bone, "wiggle_damp_use_dist", False):
        apply_taper_to_chain(context, "wiggle_damp", scene.wiggle_damp_start, scene.wiggle_damp_end)

    return None

# --- 콜백 함수들 (슬라이더용) ---

def apply_taper_logic_deferred():
    """타이머용 중간 가교 함수"""
    apply_values_safely()
    return None

def wiggle_taper_callback(self, context):
    """Stiff 슬라이더 조작 시 실행"""
    if not bpy.app.timers.is_registered(apply_taper_logic_deferred):
        bpy.app.timers.register(apply_taper_logic_deferred, first_interval=0.01)

def wiggle_damp_callback(self, context):
    """Damp 슬라이더 조작 시 실행"""
    if not bpy.app.timers.is_registered(apply_taper_logic_deferred):
        bpy.app.timers.register(apply_taper_logic_deferred, first_interval=0.01)
