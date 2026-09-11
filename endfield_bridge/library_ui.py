"""Small per-scene browser state and visible imported-instance selection."""
import json


def page(settings):
    return settings.kind if settings.category == 'PEOPLE' else settings.category


def search_kind(settings):
    """Native Sora-Core kind filter for the current library category, or None."""
    if settings.category == 'PEOPLE':
        return settings.kind
    if settings.category == 'ITEMS':
        return 'weapon'
    return None


def pages(settings):
    return json.loads(settings.library_pages or '{}')


def remember(settings):
    records = pages(settings)
    key = settings.library_page or page(settings)
    record = records.setdefault(key, {})
    record.update(query=settings.query, details=settings.asset_details)
    if 0 <= settings.selected < len(settings.assets):
        record['selected'] = settings.assets[settings.selected].identity
    settings.library_pages = json.dumps(records)


def clear_rows(settings):
    settings.assets.clear()
    settings.selected = -1
    settings.result_database = ''
    settings.offset = 0
    settings.total = 0


def switch_page(settings, context):
    if settings.library_switching:
        return
    remember(settings)
    records = pages(settings)
    key = page(settings)
    saved = records.get(key, {})
    settings.library_switching = True
    try:
        clear_rows(settings)
        settings.library_page = key
        settings.query = saved.get('query', '')
        settings.asset_details = saved.get('details', False)
    finally:
        settings.library_switching = False


def query_changed(settings, context):
    if settings.library_switching:
        return
    records = pages(settings)
    saved = records.setdefault(page(settings), {})
    saved.update(query=settings.query, selected='')
    settings.library_pages = json.dumps(records)
    clear_rows(settings)


def details_changed(settings, context):
    if not settings.library_switching:
        remember(settings)


def source_changed(settings):
    records = pages(settings)
    for record in records.values():
        record['selected'] = ''
    settings.library_pages = json.dumps(records)
    clear_rows(settings)


def restore_selection(settings):
    identity = pages(settings).get(page(settings), {}).get('selected', '')
    settings.selected = next((i for i,row in enumerate(settings.assets) if row.identity == identity),
                             0 if settings.assets else -1)
    remember(settings)


def visible_roots(context):
    collections = [context.scene.collection, *context.scene.collection.children_recursive]
    return [c for c in collections if c.get('sora_instance') and not c.get('sora_owner_collection')
            and not c.get('sora_equipment_role') and visible_objects(context,c)]


def visible_objects(context, collection):
    return [o for o in collection.objects if not o.get('sora_owner_collection')
            and not o.get('sora_display_source') and not o.get('sora_attachment_root')
            and o.name in context.view_layer.objects and o.visible_get(view_layer=context.view_layer)
            and not o.hide_select]


def select(context, token):
    if context.mode != 'OBJECT':
        raise ValueError('Switch to Object Mode before selecting another instance')
    matches = [c for c in visible_roots(context) if c.get('sora_instance') == token]
    if len(matches) != 1:
        raise ValueError('Instance is hidden, removed or has a duplicate identity; refresh selection')
    objects = visible_objects(context,matches[0])
    obj = next((o for o in objects if o.type == 'ARMATURE'),objects[0])
    for selected in context.selected_objects:
        selected.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj


_instance_items = []


def instance_items(operator, context):
    global _instance_items
    frozen = getattr(operator, '_instance_choices', None)
    if frozen is not None:
        return frozen
    if context is None:
        return _instance_items
    _instance_items = [(str(c['sora_instance']), c.name + ' · ' + str(c['sora_instance'])[:8],
                        'Select visible imported instance ' + str(c['sora_instance']))
                       for c in visible_roots(context)]
    return _instance_items
