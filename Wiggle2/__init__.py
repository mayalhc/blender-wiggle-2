import bpy
from . import wiggle_2
from . import ui_panel
from . import physics_logic
from . import wiggle_layers
from . import wiggle_lattice_visual
from .physics_logic import wiggle_taper_callback, wiggle_damp_callback

def register():
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

    if not hasattr(bpy.types.Scene, "wiggle_use_gpu"):
        bpy.types.Scene.wiggle_use_gpu = bpy.props.BoolProperty(
            name="GPU Active",
            default=False
        )

    if not hasattr(bpy.types.Scene, "wiggle_use_lattice"):
        bpy.types.Scene.wiggle_use_lattice = bpy.props.BoolProperty(
            name="Enable Horizontal Lattice",
            description="Connects adjacent bones horizontally to prevent skirt twists and mesh pinching",
            default=False,
            update=wiggle_lattice_visual.update_lattice_toggle
        )
    else:
        bpy.types.Scene.wiggle_use_lattice = bpy.props.BoolProperty(
            name="Enable Horizontal Lattice",
            description="Connects adjacent bones horizontally to prevent skirt twists and mesh pinching",
            default=False,
            update=wiggle_lattice_visual.update_lattice_toggle
        )

    if not hasattr(bpy.types.Scene, "wiggle_lattice_show_debug"):
        bpy.types.Scene.wiggle_lattice_show_debug = bpy.props.BoolProperty(
            name="Show Lattice Guide",
            description="Draws real-time light blue debug lines between connected bone chains",
            default=True,
            update=wiggle_lattice_visual.update_lattice_show_debug
        )
    else:
        bpy.types.Scene.wiggle_lattice_show_debug = bpy.props.BoolProperty(
            name="Show Lattice Guide",
            description="Draws real-time light blue debug lines between connected bone chains",
            default=True,
            update=wiggle_lattice_visual.update_lattice_show_debug
        )

    if not hasattr(bpy.types.Scene, "wiggle_lattice_stiffness"):
        bpy.types.Scene.wiggle_lattice_stiffness = bpy.props.FloatProperty(
            name="Lattice Stiffness",
            description="Strength of the horizontal stabilizer (higher values resist cross-chain stretching)",
            default=0.05,
            min=0.0,
            max=1.0
        )

    try:
        physics_logic.register()
    except:
        pass
    try:
        wiggle_2.register()
    except ValueError:
        pass
    try:
        ui_panel.register()
    except ValueError:
        pass
    try:
        wiggle_layers.register()
    except ValueError:
        pass
    try:
        wiggle_lattice_visual.register()
    except:
        pass

def unregister():
    try:
        wiggle_lattice_visual.unregister()
    except:
        pass
    try:
        wiggle_layers.unregister()
    except:
        pass
    try:
        ui_panel.unregister()
    except:
        pass
    try:
        wiggle_2.unregister()
    except:
        pass
    try:
        physics_logic.unregister()
    except:
        pass

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

    if hasattr(bpy.types.Scene, "wiggle_use_lattice"):
        del bpy.types.Scene.wiggle_use_lattice
    if hasattr(bpy.types.Scene, "wiggle_lattice_show_debug"):
        del bpy.types.Scene.wiggle_lattice_show_debug
    if hasattr(bpy.types.Scene, "wiggle_lattice_stiffness"):
        del bpy.types.Scene.wiggle_lattice_stiffness
