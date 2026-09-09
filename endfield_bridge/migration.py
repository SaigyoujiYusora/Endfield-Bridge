"""Conservative migration of saved animation-only data source settings."""

def migrate_scene(scene, resolve, is_directory):
    shared = getattr(scene, 'sora', None)
    animation = getattr(scene, 'sora_animation', None)
    if shared is None or animation is None or shared.game_root.strip():
        return False
    legacy = animation.game_root.strip()
    if not legacy:
        return False
    try:
        root = resolve(legacy)
        if not is_directory(root):
            return False
    except (OSError, ValueError, TypeError):
        return False
    shared.game_root = root
    # Results acquired before the unified source was established are stale.
    shared.assets.clear()
    shared.selected = -1
    shared.result_database = ''
    shared.offset = 0
    shared.total = 0
    animation.rows.clear()
    animation.selected = -1
    animation.clips.clear()
    animation.selected_clip = -1
    animation.clip_resource = ''
    animation.clip_root = ''
    shared.status = 'Migrated saved animation Game Folder; load / search the database'
    return True
