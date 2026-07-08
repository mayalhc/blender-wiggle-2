import bpy
from . import wiggle_2
from . import ui_panel
from . import physics_logic
from . import wiggle_layers
from . import wiggle_lattice_visual
from .physics_logic import wiggle_taper_callback, wiggle_damp_callback

# ============================================================
# __init__.py  (v4)
# ▣ 모든 속성을 hasattr 가드로 단일 등록
# ▣ 중복 정의 완전 제거
# ▣ RTX Turbo(physics_logic의 가짜 GPU 엔진)는 제거된 상태로 유지.
#   Horizontal Lattice는 사용자 요청으로 원본 그대로 복원됨.
# ============================================================

def register():
    # ── PoseBone 속성 ──────────────────────────────────────
    if not hasattr(bpy.types.PoseBone, "wiggle_stiff_use_dist"):
        bpy.types.PoseBone.wiggle_stiff_use_dist = bpy.props.BoolProperty(
            name="Use Stiff Dist.",
            default=False,
            description="Root-Tip distribution for stiffness"
        )
    if not hasattr(bpy.types.PoseBone, "wiggle_damp_use_dist"):
        bpy.types.PoseBone.wiggle_damp_use_dist = bpy.props.BoolProperty(
            name="Use Damp Dist.",
            default=False,
            description="Root-Tip distribution for damping"
        )

    # ── Scene 속성 — Taper 슬라이더 ────────────────────────
    if not hasattr(bpy.types.Scene, "wiggle_stiff_start"):
        bpy.types.Scene.wiggle_stiff_start = bpy.props.FloatProperty(
            name="Stiff Root", default=10.0, min=0.0,
            update=wiggle_taper_callback
        )
    if not hasattr(bpy.types.Scene, "wiggle_stiff_end"):
        bpy.types.Scene.wiggle_stiff_end = bpy.props.FloatProperty(
            name="Stiff Tip", default=10.0, min=0.0,
            update=wiggle_taper_callback
        )
    if not hasattr(bpy.types.Scene, "wiggle_damp_start"):
        bpy.types.Scene.wiggle_damp_start = bpy.props.FloatProperty(
            name="Damp Root", default=1.0, min=0.0,
            update=wiggle_damp_callback
        )
    if not hasattr(bpy.types.Scene, "wiggle_damp_end"):
        bpy.types.Scene.wiggle_damp_end = bpy.props.FloatProperty(
            name="Damp Tip", default=1.0, min=0.0,
            update=wiggle_damp_callback
        )

    # ── Scene 속성 — Horizontal Lattice ──────────────────────
    if not hasattr(bpy.types.Scene, "wiggle_use_lattice"):
        bpy.types.Scene.wiggle_use_lattice = bpy.props.BoolProperty(
            name="Enable Horizontal Lattice",
            description="Connects adjacent bones horizontally",
            default=False,
            update=wiggle_lattice_visual.update_lattice_toggle
        )
    if not hasattr(bpy.types.Scene, "wiggle_lattice_show_debug"):
        bpy.types.Scene.wiggle_lattice_show_debug = bpy.props.BoolProperty(
            name="Show Lattice Guide",
            description="Draws real-time debug lines between chains",
            default=True,
            update=wiggle_lattice_visual.update_lattice_show_debug
        )
    if not hasattr(bpy.types.Scene, "wiggle_lattice_stiffness"):
        bpy.types.Scene.wiggle_lattice_stiffness = bpy.props.FloatProperty(
            name="Lattice Stiffness",
            description="Horizontal stabilizer strength",
            default=0.05, min=0.0, max=1.0
        )

    # ── 서브모듈 등록 (순서: layers → wiggle_2 → ui → lattice) ─
    for mod in (physics_logic, wiggle_layers, wiggle_2, ui_panel, wiggle_lattice_visual):
        try:
            mod.register()
        except (ValueError, RuntimeError) as e:
            print(f"Wiggle2: {mod.__name__} register warning: {e}")


def unregister():
    for mod in (wiggle_lattice_visual, ui_panel, wiggle_2, wiggle_layers, physics_logic):
        try:
            mod.unregister()
        except Exception:
            pass

    for attr in ("wiggle_stiff_use_dist", "wiggle_damp_use_dist"):
        if hasattr(bpy.types.PoseBone, attr):
            try: delattr(bpy.types.PoseBone, attr)
            except Exception: pass

    for attr in ("wiggle_stiff_start", "wiggle_stiff_end",
                 "wiggle_damp_start", "wiggle_damp_end",
                 "wiggle_use_lattice", "wiggle_lattice_show_debug", "wiggle_lattice_stiffness"):
        if hasattr(bpy.types.Scene, attr):
            try: delattr(bpy.types.Scene, attr)
            except Exception: pass
