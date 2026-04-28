import bpy
from . import wiggle_2 
from . import ui_panel 

def register():
    wiggle_2.register() 
    ui_panel.register() 

def unregister():
    ui_panel.unregister()
    wiggle_2.unregister()
