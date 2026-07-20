import bpy


def wiggle_taper_callback(self, context):
    # Dragging the Root/Tip sliders re-applies to the active bone's chain
    # immediately via the same apply_taper_to_chain logic used by the
    # preset buttons. This callback used to be a no-op, so turning on
    # wiggle_stiff_use_dist and moving the slider had no effect.
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
    # Auto-register required properties (skip if another module already did).
    if not hasattr(bpy.types.Scene, "wiggle_enable"):
        bpy.types.Scene.wiggle_enable = bpy.props.BoolProperty(default=False)
    if not hasattr(bpy.types.Object, "wiggle_mute"):
        bpy.types.Object.wiggle_mute = bpy.props.BoolProperty(default=False)

def unregister():
    pass
