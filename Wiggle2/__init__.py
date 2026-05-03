import bpy
from . import wiggle_2 
from . import ui_panel 
from . import physics_logic 

# Load callback functions for Stiff and Damp from the logic file
from .physics_logic import wiggle_taper_callback, wiggle_damp_callback

def register():
    # 1. PoseBone Properties (Taper Mode Toggles) - [원본 유지]
    bpy.types.PoseBone.wiggle_stiff_use_dist = bpy.props.BoolProperty(
        name="Use Stiff Dist.",
        default=False,
        description="Enable to use Root-Tip distribution for stiffness; disable to use a single value"
    )

    bpy.types.PoseBone.wiggle_damp_use_dist = bpy.props.BoolProperty(
        name="Use Damp Dist.",
        default=False,
        description="Enable to use Root-Tip distribution for damping; disable to use a single value"
    )

    # 2. Scene Properties for Stiffness (Stiff) Distribution - [원본 유지]
    bpy.types.Scene.wiggle_stiff_start = bpy.props.FloatProperty(
        name="Stiff Root",
        description="Stiffness value at the start (root) of the bone chain",
        default=10.0,
        min=0.0,
        update=wiggle_taper_callback 
    )
    bpy.types.Scene.wiggle_stiff_end = bpy.props.FloatProperty(
        name="Stiff Tip",
        description="Stiffness value at the end (tip) of the bone chain",
        default=10.0,
        min=0.0,
        update=wiggle_taper_callback
    )

    # 3. Scene Properties for Damping (Damp) Distribution - [원본 유지]
    bpy.types.Scene.wiggle_damp_start = bpy.props.FloatProperty(
        name="Damp Root",
        description="Damping value at the start (root) of the bone chain",
        default=1.0,
        min=0.0,
        update=wiggle_damp_callback 
    )
    bpy.types.Scene.wiggle_damp_end = bpy.props.FloatProperty(
        name="Damp Tip",
        description="Damping value at the end (tip) of the bone chain",
        default=1.0,
        min=0.0,
        update=wiggle_damp_callback
    )

    # [RTX 추가] GPU 사용 여부를 저장할 속성
    if not hasattr(bpy.types.Scene, "wiggle_use_gpu"):
        bpy.types.Scene.wiggle_use_gpu = bpy.props.BoolProperty(
            name="GPU Active",
            default=False
        )

    # 4. Register existing modules - [에러 방지 강화]
    # 하나가 에러나도 나머지는 등록되도록 try-except 처리
    try:
        physics_logic.register() 
    except:
        pass

    try:
        wiggle_2.register() 
    except ValueError:
        # 'already registered' 에러 발생 시 무시하고 진행
        pass

    try:
        ui_panel.register() 
    except ValueError:
        pass

def unregister():
    # 1. Unregister existing modules
    try: ui_panel.unregister()
    except: pass
    
    try: wiggle_2.unregister()
    except: pass
    
    try: physics_logic.unregister()
    except: pass

    # 2. Remove registered properties for memory cleanup
    if hasattr(bpy.types.PoseBone, "wiggle_stiff_use_dist"):
        del bpy.types.PoseBone.wiggle_stiff_use_dist
    if hasattr(bpy.types.PoseBone, "wiggle_damp_use_dist"):
        del bpy.types.PoseBone.wiggle_damp_use_dist

    if hasattr(bpy.types.Scene, "wiggle_stiff_start"):
        del bpy.types.Scene.wiggle_stiff_start
    if hasattr(bpy.types.Scene, "wiggle_stiff_end"):
        del bpy.types.Scene.wiggle_stiff_end
    if hasattr(bpy.types.Scene, "wiggle_damp_start"):
        del bpy.types.Scene.wiggle_damp_start
    if hasattr(bpy.types.Scene, "wiggle_damp_end"):
        del bpy.types.Scene.wiggle_damp_end
    if hasattr(bpy.types.Scene, "wiggle_use_gpu"):
        del bpy.types.Scene.wiggle_use_gpu
