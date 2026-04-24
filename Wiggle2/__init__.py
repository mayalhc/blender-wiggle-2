import bpy
from . import wiggle_2 

def register():
    wiggle_2.register()

def unregister():
    wiggle_2.unregister()

if __name__ == "__main__":
    register()
