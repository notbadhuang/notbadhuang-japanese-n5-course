"""Read-only presentation of diagnostic evidence, never a replacement scorer."""


FRIENDLY_TITLES = {
    'N5-REA-002': '理解中等长度的日常文本',
    'N5-REA-003': '从通知等材料中查找信息',
    'N5-LIS-002': '听出指定的关键信息',
}


def present(diagnostic, responses, stage):
    events = diagnostic.response_events({'responses': responses})
    evaluation = diagnostic.evaluate_session(
        {'session_id': 'presentation-only', 'mode': 'short_placement', 'response_events': events},
        model=diagnostic.MODEL, bank=diagnostic.BANK)
    route = evaluation['short_screen_outcome']
    progress = {'current_number': len(events) + 1,
                'maximum_item_count': diagnostic.MODEL['short_route_limits']['maximum_scored_items'],
                'total_item_count': None, 'remaining_item_count': None}
    # These branches finish after their already chosen probe set; anchor branches
    # can still require further items, so must never advertise a fixed total.
    if stage == 'question' and route['reason_code'] in {
        'foundation_script_boundary_probes_required', 'foundation_full_profile_required',
        'core_language_boundary_probes_required', 'receptive_boundary_probes_required',
    }:
        answered = {e['diagnostic_item_id'] for e in events}
        remaining = len(set(route['next_item_ids']) - answered)
        progress.update(total_item_count=len(events) + remaining, remaining_item_count=remaining)
    result = {'progress': progress}
    if stage != 'diagnostic_result':
        return result
    groups = {name: [] for name in ('initial_pass', 'priority_practice', 'needs_confirmation', 'not_tested')}
    for row in evaluation['ability_evidence']:
        count, correct = row['attempted_item_count'], row['correct_item_count']
        if row['invalid_item_count']:
            group = 'needs_confirmation'
        elif not count:
            group = 'not_tested'
        elif row['prerequisite_block_ids']:
            group = 'needs_confirmation'
        elif count == correct:
            group = 'initial_pass'
        elif row['status'] == 'emerging':
            group = 'priority_practice'
        else:
            group = 'needs_confirmation'
        aid = row['ability_point_id']
        groups[group].append(dict(ability_point_id=aid,
            title_zh=FRIENDLY_TITLES.get(aid, diagnostic.ABILITY_TITLES[aid]),
            attempted_count=count, correct_count=correct,
            invalid_count=row['invalid_item_count'], blocked=bool(row['prerequisite_block_ids'])))
    result.update(groups=groups, answered_count=len(events),
                  tested_count=sum(bool(r['attempted_item_count']) for r in evaluation['ability_evidence']),
                  planned_count=len(diagnostic.MODEL['language_ability_order']))
    return result
