import ast
import json
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]/'endfield_bridge'
tree=ast.parse((ROOT/'generic_weapons.py').read_text(encoding='utf-8'))
names={'adaptation_grade','adaptation_icon','adaptation_note','selection_index','selection_status','result_context','display_name'}
ns={'json':json}
exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names],type_ignores=[]),'<weapon selection>','exec'),ns)


class GenericWeaponSelectionTests(unittest.TestCase):
    def test_grades_are_explicit_and_never_promote_unknown(self):
        self.assertEqual(ns['adaptation_grade']('confirmed'),'确定')
        self.assertEqual(ns['adaptation_grade']('inferred'),'推断')
        for value in ('unknown','',None,'future-confidence'):
            with self.subTest(value=value):
                self.assertEqual(ns['adaptation_grade'](value),'未知')
                self.assertEqual(ns['adaptation_icon'](value),'QUESTION')

    def test_notes_distinguish_ambiguous_refined_from_confirmed_and_conflict(self):
        self.assertEqual(ns['adaptation_note']('confirmed','exact'),'原生资源精确匹配')
        self.assertIn('基础归属不明确',ns['adaptation_note']('inferred','base ownership is ambiguous'))
        self.assertIn('未验证实际挂载',ns['adaptation_note']('inferred','agree on the type'))
        self.assertIn('冲突',ns['adaptation_note']('unknown','associations disagree'))
        self.assertIn('缺少完整',ns['adaptation_note']('unknown','Only the base resource path resolves'))
        self.assertIn('缺少原生类型依据',ns['adaptation_note']('unknown','no declared row'))

    def test_selection_follows_stable_identity_across_reorder(self):
        first=['address:aaa','address:bbb']
        self.assertEqual(ns['selection_index'](first,'address:bbb'),1)
        reordered=['address:bbb','address:aaa']
        self.assertEqual(ns['selection_index'](reordered,'address:bbb'),0)
        self.assertEqual(ns['selection_index'](reordered,'address:aaa'),1)

    def test_selection_fails_closed_when_identity_disappears(self):
        self.assertEqual(ns['selection_index'](['address:aaa'],'address:missing'),-1)
        self.assertEqual(ns['selection_index']([],'address:aaa'),-1)
        index,warning=ns['selection_status'](['address:aaa'],'address:missing','','',32)
        self.assertEqual(index,-1)
        self.assertIn('不在当前页',warning)

    def test_filter_exclusion_is_reported_as_explicit_failure(self):
        index,warning=ns['selection_status'](['address:aaa'],'address:bbb','other-query','sword',1)
        self.assertEqual(index,-1)
        self.assertIn('筛选排除',warning)

    def test_empty_selected_query_to_nonempty_filter_reports_exclusion(self):
        # Real flow: select under query='' then filter to a 2-row result set shown whole on one page.
        index,warning=ns['selection_status'](['address:aaa','address:bbb'],'address:old','wpn_funnel_0008','',2)
        self.assertEqual(index,-1)
        self.assertIn('筛选排除',warning)
        self.assertNotIn('可翻页找回',warning)
        # Clearing a filter that still leaves more results than one page stays a paging case.
        index,warning=ns['selection_status'](['x']*30,'address:old','','wpn_funnel',32)
        self.assertEqual(index,-1)
        self.assertIn('不在当前页',warning)

    def test_empty_desired_selection_defaults_to_first_row_only(self):
        self.assertEqual(ns['selection_index'](['address:aaa'],''),0)
        self.assertEqual(ns['selection_index']([],''),-1)

    def test_display_name_avoids_duplicated_refined_identifiers(self):
        self.assertEqual(ns['display_name']('孤舟','wpn_funnel_0002'),'孤舟 / wpn_funnel_0002')
        self.assertEqual(ns['display_name']('wpn_funnel_0008_refined','wpn_funnel_0008_refined'),'wpn_funnel_0008_refined')

    def test_result_context_fences_instance_database_root_and_query(self):
        base=ns['result_context']('inst','db','root','q')
        self.assertEqual(base,ns['result_context']('inst','db','root','q'))
        for changed in (('other','db','root','q'),('inst','db2','root','q'),('inst','db','root2','q'),('inst','db','root','q2')):
            with self.subTest(changed=changed):
                self.assertNotEqual(base,ns['result_context'](*changed))


if __name__=='__main__':unittest.main()
