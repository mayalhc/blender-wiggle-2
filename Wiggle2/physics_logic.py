import bpy


def wiggle_taper_callback(self, context):
    # Root/Tip 슬라이더를 드래그할 때, 프리셋 버튼과 동일한 apply_taper_to_chain
    # 로직으로 활성 본의 체인에 즉시 재적용한다. 기존에는 콜백이 no-op라서
    # wiggle_stiff_use_dist 토글을 켜도 슬라이더를 움직이면 아무 효과가 없었음.
    bone = getattr(context, "active_pose_bone", None)
    if not bone or not getattr(bone, "wiggle_stiff_use_dist", False):
        return
    apply_taper_to_chain(context, "wiggle_stiff", self.wiggle_stiff_start, self.wiggle_stiff_end)

def wiggle_damp_callback(self, context):
    bone = getattr(context, "active_pose_bone", None)
    if not bone or not getattr(bone, "wiggle_damp_use_dist", False):
        return
    apply_taper_to_chain(context, "wiggle_damp", self.wiggle_damp_start, self.wiggle_damp_end)

def apply_taper_to_chain(target, attr, start_val, end_val):
    bone = target.active_pose_bone if hasattr(target, "active_pose_bone") else target
    if not bone: return
    chain = []
    curr = bone
    while curr:
        chain.append(curr)
        if hasattr(curr, "children") and len(curr.children) > 0:
            curr = curr.children[0]
        else: curr = None
    if not chain: return
    for i, b in enumerate(chain):
        t = i / (len(chain) - 1) if len(chain) > 1 else 0
        try: setattr(b, attr, start_val + (end_val - start_val) * t)
        except: pass


def register():
    # 필수 속성 자동 등록 (다른 모듈에서 이미 등록했으면 건너뜀)
    if not hasattr(bpy.types.Scene, "wiggle_enable"):
        bpy.types.Scene.wiggle_enable = bpy.props.BoolProperty(default=False)
    if not hasattr(bpy.types.Object, "wiggle_mute"):
        bpy.types.Object.wiggle_mute = bpy.props.BoolProperty(default=False)

def unregister():
    pass
