"""Summarize docking searches; repetitions measure search variability, not biology."""
from pathlib import Path
from itertools import combinations
import json
import numpy as np
import pandas as pd
from rdkit import Chem
from rdkit.Chem import rdMolAlign

import os
OUT=Path(os.environ['SYNBIOPILOT_MOLECULAR_OUTPUT']).resolve()
ZN=np.array([19.284,-18.126,-2.875])
top=pd.read_csv(OUT/'tables/top1_per_seed.csv')
properties=pd.read_csv(OUT/'tables/compound_properties.csv')
receptor=(OUT/'docking/receptorH.pdb').read_text()
atoms=[]
for line in receptor.splitlines():
    if line.startswith('ATOM') and line[76:78].strip()!='H':
        atoms.append((int(line[22:26]),line[17:20].strip(),[float(line[30:38]),float(line[38:46]),float(line[46:54])]))
xyz=np.array([a[2] for a in atoms]);resids=sorted(set(a[0] for a in atoms))
resnames={a[0]:a[1] for a in atoms}
contacts=[];heteros=[];pairs=[];summaries=[];modeldata={};zbg_rows=[]
queries={'Hydroxamate':Chem.MolFromSmarts('C(=[O:1])-N-[O:2]'),
         'Aminobenzamide':Chem.MolFromSmarts('[N;H2:1]-c1ccccc1-N-C(=[O:2])')}
for name,group in top.groupby('compound'):
    mols={}
    for row in group.itertuples():
        path=OUT/'structures'/f'{name}_{row.seed}_top1.sdf'
        m=Chem.SDMolSupplier(str(path),removeHs=False)[0];mols[row.seed]=m
        pos=m.GetConformer().GetPositions()
        family=properties.set_index('compound').loc[name,'family']
        if family in queries:
            query=queries[family];match=m.GetSubstructMatch(query)
            if not match:raise RuntimeError(f'Cannot locate {family} group in {name}')
            donors=[match[a.GetIdx()] for a in query.GetAtoms() if a.GetAtomMapNum()]
            distances=[float(np.linalg.norm(pos[i]-ZN)) for i in donors]
            zbg_rows.append(dict(compound=name,seed=row.seed,zbg_family=family,
                donor1_index_1based=donors[0]+1,donor1_element=m.GetAtomWithIdx(donors[0]).GetSymbol(),donor1_Zn_A=distances[0],
                donor2_index_1based=donors[1]+1,donor2_element=m.GetAtomWithIdx(donors[1]).GetSymbol(),donor2_Zn_A=distances[1],
                closest_ZBG_Zn_A=min(distances),farthest_ZBG_Zn_A=max(distances),
                nearest_NOS_is_expected_ZBG=int(row.zn_nearest_atom_index_1based-1 in donors)))
        heavy=[a.GetIdx() for a in m.GetAtoms() if a.GetAtomicNum()>1]
        d=np.linalg.norm(pos[heavy,None,:]-xyz[None,:,:],axis=2).min(axis=0)
        for resid in resids:
            md=float(min(d[i] for i,a in enumerate(atoms) if a[0]==resid))
            contacts.append(dict(compound=name,seed=row.seed,resid=resid,resname=resnames[resid],min_heavy_distance_A=md,within_4A=int(md<=4)))
        for atom in m.GetAtoms():
            if atom.GetSymbol() not in ['N','O','S']:continue
            idx=atom.GetIdx();dist=float(np.linalg.norm(pos[idx]-ZN))
            env=Chem.FindAtomEnvironmentOfRadiusN(m,1,idx)
            frag=Chem.MolFragmentToSmiles(m,atomsToUse=sorted({idx}|{n.GetIdx() for n in atom.GetNeighbors() if n.GetAtomicNum()>1}),isomericSmiles=True)
            heteros.append(dict(compound=name,seed=row.seed,atom_index_1based=idx+1,element=atom.GetSymbol(),zn_distance_A=dist,local_fragment=frag,aromatic=atom.GetIsAromatic()))
        modeldata[f'{name}_{row.seed}']={'sdf':path.read_text(),'score':row.score_kcal_mol,'zn':row.zn_nearest_NOS_A}
    distances=[]
    for a,b in combinations(sorted(mols),2):
        value=rdMolAlign.CalcRMS(Chem.RemoveHs(mols[a]),Chem.RemoveHs(mols[b]),maxMatches=100000)
        pairs.append(dict(compound=name,seed_a=a,seed_b=b,direct_symmetry_RMSD_A=value));distances.append(value)
    nearest=pd.DataFrame([h for h in heteros if h['compound']==name]).sort_values('zn_distance_A').groupby('seed').first()
    summaries.append(dict(compound=name,n_searches=len(group),score_mean_kcal_mol=group.score_kcal_mol.mean(),score_sd_kcal_mol=group.score_kcal_mol.std(ddof=1),score_min_kcal_mol=group.score_kcal_mol.min(),score_max_kcal_mol=group.score_kcal_mol.max(),zn_mean_A=group.zn_nearest_NOS_A.mean(),zn_min_A=group.zn_nearest_NOS_A.min(),zn_max_A=group.zn_nearest_NOS_A.max(),pair_RMSD_mean_A=np.mean(distances),pair_RMSD_max_A=max(distances),nearest_atom_fragments='; '.join(sorted(set(nearest.local_fragment)))))
summary=pd.DataFrame(summaries).merge(properties,on='compound')
zbg=pd.DataFrame(zbg_rows)
zg=zbg.groupby('compound').agg(expected_ZBG_min_mean_A=('closest_ZBG_Zn_A','mean'),expected_ZBG_min_min_A=('closest_ZBG_Zn_A','min'),expected_ZBG_min_max_A=('closest_ZBG_Zn_A','max'),searches_nearest_atom_is_ZBG=('nearest_NOS_is_expected_ZBG','sum')).reset_index()
summary=summary.merge(zg,on='compound',how='left')
zbg.to_csv(OUT/'tables/expected_Zn_binding_group_geometry.csv',index=False)
all_zbg=[]
for name,group in top.groupby('compound'):
    family=properties.set_index('compound').loc[name,'family']
    if family not in queries:continue
    query=queries[family]
    for row in group.itertuples():
        for rank,m in enumerate(Chem.SDMolSupplier(str(OUT/'structures'/f'{name}_{row.seed}_allposes.sdf'),removeHs=False),1):
            match=m.GetSubstructMatch(query);donors=[match[a.GetIdx()] for a in query.GetAtoms() if a.GetAtomMapNum()]
            distances=[float(np.linalg.norm(m.GetConformer().GetPositions()[i]-ZN)) for i in donors]
            all_zbg.append(dict(compound=name,seed=row.seed,rank=rank,score_kcal_mol=float(m.GetProp('AD4Zn_score_kcal_mol')),closest_ZBG_Zn_A=min(distances),farthest_ZBG_Zn_A=max(distances),geometric_filter_pass=int(min(distances)<=2.8 and max(distances)<=3.0)))
all_zbg=pd.DataFrame(all_zbg)
all_zbg.to_csv(OUT/'tables/all_modes_ZBG_geometry.csv',index=False)
filtered=all_zbg[all_zbg.geometric_filter_pass==1].sort_values(['compound','seed','rank']).groupby(['compound','seed']).first().reset_index()
filtered.to_csv(OUT/'tables/geometry_filtered_candidates.csv',index=False)
summary.to_csv(OUT/'tables/compound_summary.csv',index=False)
pd.DataFrame(pairs).to_csv(OUT/'tables/pairwise_pose_RMSD.csv',index=False)
pd.DataFrame(heteros).to_csv(OUT/'tables/zn_all_heteroatom_distances.csv',index=False)
contact=pd.DataFrame(contacts)
contact.to_csv(OUT/'tables/protein_contacts_per_search.csv',index=False)
frequency=contact.groupby(['compound','resid','resname']).agg(searches_with_contact=('within_4A','sum'),n_searches=('within_4A','count'),min_distance_A=('min_heavy_distance_A','min')).reset_index()
frequency.to_csv(OUT/'tables/protein_contact_search_counts.csv',index=False)
candidates=pd.DataFrame(json.loads((OUT/'inputs/commercial_candidates.json').read_text()))
candidates.to_csv(OUT/'tables/commercial_candidates.csv',index=False)
with pd.ExcelWriter(OUT/'tables/HDAC2_Docking_Comparison.xlsx',engine='openpyxl') as writer:
    for sheet,data in [('Summary',summary),('Commercial_candidates',candidates),('Top1_each_seed',top),('Pose_consistency',pd.DataFrame(pairs)),('Zn_each_heteroatom',pd.DataFrame(heteros)),('Expected_Zn_binding_group',zbg),('Filtered_candidates',filtered),('All_modes_ZBG',all_zbg),('Contacts_4A',frequency),('All_modes',pd.read_csv(OUT/'tables/all_docking_poses.csv')),('Molecule_properties',properties)]:
        data.to_excel(writer,sheet_name=sheet,index=False)
        ws=writer.sheets[sheet];ws.freeze_panes='A2';ws.auto_filter.ref=ws.dimensions
        for col in ws.columns:ws.column_dimensions[col[0].column_letter].width=min(60,max(15,len(str(col[0].value))+2))
print(summary[['compound','score_mean_kcal_mol','score_sd_kcal_mol','zn_mean_A','pair_RMSD_max_A','nearest_atom_fragments']].to_string(index=False))
print(zg.to_string(index=False))
print('Separate geometry-filtered candidates (not replacements for top-ranked poses):')
print(filtered[['compound','seed','rank','score_kcal_mol']].to_string(index=False))

# An optional browser viewer displays the actual SDF coordinates in the fixed
# receptor frame. The local 3Dmol.js distribution is packaged with the results.
html='''<!doctype html><html lang="zh"><meta charset="utf-8"><title>HDAC2 对接姿势</title>
<script src="assets/3Dmol-min.js"></script><style>body{font:16px system-ui;margin:24px;color:#183040}select,button{font:inherit;margin:6px;padding:6px}#viewer{height:650px;width:100%;position:relative;border:1px solid #ccd3d9}p{max-width:1100px;line-height:1.6}</style>
<h1>HDAC2 商用抑制剂与 R-SFN：对接姿势</h1>
<p>选择化合物与搜索种子，查看该次搜索的第一名姿势。灰色蛋白；紫色球为 Zn；橙色碳为选中配体；青色碳为可选晶体 SAHA；绿色碳为可选 R-SFN。坐标保持同一受体参考系。鼠标拖动旋转、滚轮缩放。全部模型可在 structures 文件夹中下载。</p>
<select id="compound"></select><select id="seed"><option>20261021</option><option>20261022</option><option>20261023</option></select>
<label><input type="checkbox" id="crystal" checked>晶体 SAHA</label> <label><input type="checkbox" id="sfn">R-SFN 同种子</label><button id="reset">重置视角</button>
<p id="numbers"></p><div id="viewer"></div>
<p>主比较包含 9 种中性母体，Panobinostat +1 用于质子化敏感性分析。页面展示 AD4Zn 评分、最近 N/O/S–Zn 距离及三次搜索的姿势一致性。</p><p>方法定义：AD4Zn 评分为计算对接指标，Zn 距离为几何量，重复搜索评价数值搜索一致性。实验亲和力、配位状态与 MD 稳定性分别使用对应测量或轨迹分析。</p>
<script>const data=__DATA__, receptor=__RECEPTOR__, crystal=__CRYSTAL__;
const compounds=__COMPOUNDS__;
const selector=document.getElementById('compound');compounds.forEach(n=>{const op=document.createElement('option');op.value=n;op.textContent=n;selector.appendChild(op)});
const viewer=$3Dmol.createViewer('viewer',{backgroundColor:'white'});
function ligand(sdf,color){const m=viewer.addModel(sdf,'sdf');m.setStyle({},{stick:{radius:.18,colorscheme:{prop:'elem',map:{C:color,N:'blue',O:'red',S:'#c6a400',F:'#45aa73',H:'white'}}}});return m}
function draw(){viewer.removeAllModels();const r=viewer.addModel(receptor,'pdb');r.setStyle({hetflag:false},{cartoon:{color:'#c7cbd0',opacity:.65}});r.setStyle({elem:'Zn'},{sphere:{color:'#a34db5',radius:.65}});r.setStyle({resi:[35,103,104,144,145,146,154,155,183,209,210,269,306,307,308]},{stick:{radius:.10,colorscheme:'grayCarbon'}});
const n=selector.value,s=document.getElementById('seed').value,d=data[n+'_'+s];const m=ligand(d.sdf,'#e58d28');if(document.getElementById('crystal').checked)ligand(crystal,'#25b5bf');if(document.getElementById('sfn').checked&&n!=='SFN_R')ligand(data['SFN_R_'+s].sdf,'#35a665');document.getElementById('numbers').textContent=n+' | seed '+s+' | AD4Zn '+d.score.toFixed(3)+' kcal/mol | 最近 N/O/S–Zn '+d.zn.toFixed(2)+' Å';viewer.zoomTo({model:m});viewer.zoom(.72);viewer.render()}
['compound','seed','crystal','sfn'].forEach(id=>document.getElementById(id).onchange=draw);document.getElementById('reset').onclick=draw;draw();</script></html>'''
order=['SAHA','TSA','Panobinostat','Belinostat','Entinostat','Mocetinostat','CI994','Chidamide','SFN_R','Panobinostat_plus1']
html=html.replace('__DATA__',json.dumps(modeldata)).replace('__RECEPTOR__',json.dumps(receptor)).replace('__CRYSTAL__',json.dumps((OUT/'inputs/SAHA_reference.sdf').read_text())).replace('__COMPOUNDS__',json.dumps(order))
(OUT/'Docking_3D_Viewer.html').write_text(html)
