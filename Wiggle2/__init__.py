import bpy
from . import wiggle_2  # 같은 폴더의 wiggle_2.py를 로드

def register():
    wiggle_2.register()

def unregister():
    wiggle_2.unregister()

if __name__ == "__main__":
    register()
