"""Common-protocol HDAC2 inhibitor docking with the existing AD4Zn receptor.

Nine neutral parent compounds, three search seeds each. Panobinostat +1 is
an explicitly separate protonation sensitivity model, not a tenth drug.
Rigid receptor; scores are not experimental binding free energies.
"""
from pathlib import Path
import json,shutil,subprocess
from concurrent.futures import ThreadPoolExecutor,as_completed
import numpy as np
import pandas as pd
from rdkit import Chem
from rdkit.Chem import AllChem,rdMolAlign,rdMolDescriptors,Descriptors
from meeko import MoleculePreparation,PDBQTWriterLegacy,PDBQTMolecule,RDKitMolCreate

import os
ROOT=Path(os.environ['SYNBIOPILOT_MOLECULAR_ASSETS']).resolve()
OUT=Path(os.environ['SYNBIOPILOT_MOLECULAR_OUTPUT']).resolve()
OUT.mkdir(parents=True, exist_ok=True)
OLD=ROOT/'preparation/interaction_panel/docking_ad4zn_ZN'
D=OUT/'docking'
for sub in ['docking','inputs','tables','structures']:
    (OUT/sub).mkdir(parents=True, exist_ok=True)
SEEDS=[20261021,20261022,20261023]
IDS={'trichostatin A':'TSA','panobinostat':'Panobinostat','belinostat':'Belinostat',
     'entinostat':'Entinostat','mocetinostat':'Mocetinostat','tacedinaline':'CI994','tucidinostat':'Chidamide'}
FAMILY={**{x:'Hydroxamate' for x in ['SAHA','TSA','Panobinostat','Belinostat','Panobinostat_plus1']},
        **{x:'Aminobenzamide' for x in ['Entinostat','Mocetinostat','CI994','Chidamide']},'SFN_R':'Isothiocyanate'}
ZN=np.array([19.284,-18.126,-2.875])

def write_sdf(m,path):
    w=Chem.SDWriter(str(path));w.write(m);w.close()

def prepare():
    for name in ['compound_structures.json', 'commercial_candidates.json']:
        shutil.copy2(Path(__file__).parent/'inputs'/name, OUT/'inputs'/name)
    raw=json.loads((OUT/'inputs/compound_structures.json').read_text())
    molecules={IDS[r['name']]:Chem.MolFromSmiles(r['SMILES']) for r in raw}
    sources={IDS[r['name']]:r['structure_source_url'] for r in raw}
    for name,fn in [('SAHA',ROOT/'preparation/interaction_panel/ligands/SAH.sdf'),('SFN_R',ROOT/'input/ligands/SFN_R.sdf')]:
        molecules[name]=Chem.RemoveHs(Chem.SDMolSupplier(str(fn),removeHs=False)[0])
        sources[name]=str(fn.relative_to(ROOT));shutil.copy2(fn,OUT/'inputs'/f'{name}_reference.sdf')
    # Predeclare a secondary-amine protonation sensitivity, separate from the
    # neutral-parent comparison. No numerical pKa or dominant fraction assumed.
    m=Chem.Mol(molecules['Panobinostat'])
    atom=[a for a in m.GetAtoms() if a.GetSymbol()=='N' and not a.GetIsAromatic()
          and a.GetTotalNumHs()==1 and all(n.GetSymbol()=='C' and n.GetHybridization()==Chem.HybridizationType.SP3 for n in a.GetNeighbors())][0]
    atom.SetFormalCharge(1);atom.SetNumExplicitHs(2);atom.SetNoImplicit(True);Chem.SanitizeMol(m)
    molecules['Panobinostat_plus1']=m;sources['Panobinostat_plus1']=sources['Panobinostat']
    properties=[];types=set()
    for name,m in molecules.items():
        m=Chem.AddHs(m);m.RemoveAllConformers()
        # Match the existing project protocol exactly: one fresh, unbound
        # embedding and MMFF optimization, without using crystal coordinates.
        # The initial eight-conformer-minimum SAHA attempt is retained separately.
        if AllChem.EmbedMolecule(m,randomSeed=20261010,clearConfs=True)!=0:
            raise RuntimeError(f'{name}: cannot generate a conformer')
        mmff_status=AllChem.MMFFOptimizeMolecule(m)
        m.SetProp('_Name',name)
        for a in m.GetAtoms():
            info=Chem.AtomPDBResidueInfo();info.SetName(f'{a.GetSymbol()}{a.GetIdx()+1:02d}'.ljust(4));info.SetResidueName('LIG');info.SetResidueNumber(407);info.SetChainId('L');info.SetIsHeteroAtom(True);a.SetMonomerInfo(info)
        write_sdf(m,OUT/'inputs'/f'{name}.sdf')
        setup=MoleculePreparation().prepare(m)[0]
        pdbqt,ok,error=PDBQTWriterLegacy.write_string(setup)
        if not ok:raise RuntimeError(error)
        (D/f'{name}.pdbqt').write_text(pdbqt)
        types.update(l[77:].strip() for l in pdbqt.splitlines() if l.startswith(('ATOM','HETATM')))
        props=dict(compound=name,family=FAMILY[name],analysis_set='sensitivity' if name.endswith('plus1') else 'primary',
            smiles=Chem.MolToSmiles(Chem.RemoveHs(m),isomericSmiles=True),formula=rdMolDescriptors.CalcMolFormula(m),
            molecular_weight=Descriptors.MolWt(m),heavy_atoms=m.GetNumHeavyAtoms(),formal_charge=Chem.GetFormalCharge(m),
            rotatable_bonds=rdMolDescriptors.CalcNumRotatableBonds(m),mmff_status=mmff_status,source=sources[name])
        properties.append(props)
    pd.DataFrame(properties).to_csv(OUT/'tables/compound_properties.csv',index=False)
    for f in ['receptorH.pdb','receptor.pdbqt','receptor_tz.pdbqt']:
        shutil.copy2(OLD/f,D/f)
    shutil.copy2(Path(os.environ['SYNBIOPILOT_AD4ZN_DAT']),D/'AD4Zn.dat')
    lt=sorted(types|{'A','C','N','NA','OA','SA','S','HD'})
    oldlines=(OLD/'receptor.gpf').read_text().splitlines();lines=[]
    for l in oldlines:
        if l.startswith('parameter_file '):l='parameter_file AD4Zn.dat'
        if l.startswith('ligand_types '):l='ligand_types '+' '.join(lt)
        if l.startswith('map '):continue
        if l.startswith('elecmap '):lines.extend(f'map receptor_tz.{t}.map' for t in lt)
        lines.append(l)
    (D/'receptor.gpf').write_text('\n'.join(lines)+'\n')
    with (D/'autogrid.stdout.log').open('w') as f:
        subprocess.run(['autogrid4','-p','receptor.gpf','-l','receptor.glg'],cwd=D,stdout=f,stderr=subprocess.STDOUT,check=True)
    glg=(D/'receptor.glg').read_text()
    if 'Successful Completion' not in glg:raise RuntimeError('AutoGrid did not finish')
    applied=[l for l in glg.splitlines() if 'map_index(' in l and 'rec_index(' in l]
    if len(applied)!=6:raise RuntimeError('Expected six existing Zn/TZ overrides')
    print('Prepared',list(molecules),'ligand atom types',lt,flush=True)
    return list(molecules)

def rmsd(a,b):
    return rdMolAlign.CalcRMS(Chem.RemoveHs(a),Chem.RemoveHs(b),maxMatches=100000)

def dock(job):
    name,seed=job;out=D/f'{name}_{seed}.pdbqt'
    with (D/f'{name}_{seed}.log').open('w') as f:
        subprocess.run(['vina','--ligand',f'{name}.pdbqt','--maps','receptor_tz','--scoring','ad4',
            '--exhaustiveness','32','--cpu','8','--seed',str(seed),'--num_modes','20','--energy_range','4',
            '--out',out.name],cwd=D,stdout=f,stderr=subprocess.STDOUT,check=True)
    poses=RDKitMolCreate.from_pdbqt_mol(PDBQTMolecule(out.read_text(),skip_typing=True))[0]
    scores=[float(l.split()[3]) for l in out.read_text().splitlines() if l.startswith('REMARK VINA RESULT')]
    if len(scores)!=poses.GetNumConformers():raise RuntimeError('Pose/score count mismatch')
    rows=[]
    writer=Chem.SDWriter(str(OUT/'structures'/f'{name}_{seed}_allposes.sdf'))
    for rank,(conf,score) in enumerate(zip(poses.GetConformers(),scores),1):
        m=Chem.Mol(poses);m.RemoveAllConformers();m.AddConformer(conf,assignId=True)
        m.SetProp('_Name',f'{name}_seed{seed}_rank{rank}');m.SetProp('AD4Zn_score_kcal_mol',str(score));writer.write(m)
        heteros=[a.GetIdx() for a in m.GetAtoms() if a.GetSymbol() in ['N','O','S']]
        dist=np.linalg.norm(m.GetConformer().GetPositions()[heteros]-ZN,axis=1);nearest=heteros[int(dist.argmin())]
        a=m.GetAtomWithIdx(nearest)
        row=dict(compound=name,seed=seed,rank=rank,score_kcal_mol=score,zn_nearest_NOS_A=float(min(dist)),
            zn_nearest_atom_index_1based=nearest+1,zn_nearest_element=a.GetSymbol())
        if name=='SAHA':row['crystal_direct_RMSD_A']=rmsd(m,Chem.SDMolSupplier(str(OUT/'inputs/SAHA_reference.sdf'),removeHs=False)[0])
        rows.append(row)
        if rank==1:write_sdf(m,OUT/'structures'/f'{name}_{seed}_top1.sdf')
    writer.close()
    print(name,seed,'done; top1',scores[0],flush=True)
    return rows

def main():
    names=prepare();rows=[]
    # SAHA serves as the targeted pose-recovery control before interpreting the panel.
    with ThreadPoolExecutor(max_workers=3) as pool:
        for part in pool.map(dock,[('SAHA',s) for s in SEEDS]):rows.extend(part)
    gate=pd.DataFrame(rows).query('rank==1')
    if not (sum(gate.crystal_direct_RMSD_A<2)>=2 and gate.crystal_direct_RMSD_A.max()<=3 and gate.zn_nearest_NOS_A.between(1.7,2.8).all()):
        pd.DataFrame(rows).to_csv(OUT/'tables/all_docking_poses.csv',index=False)
        raise RuntimeError('Predeclared SAHA recovery criterion failed; panel not interpreted')
    print('SAHA pose-recovery control passed',gate.crystal_direct_RMSD_A.tolist(),flush=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs=[(n,s) for n in names if n!='SAHA' for s in SEEDS]
        futures=[pool.submit(dock,j) for j in jobs]
        for f in as_completed(futures):rows.extend(f.result())
    data=pd.DataFrame(rows).sort_values(['compound','seed','rank'])
    data.to_csv(OUT/'tables/all_docking_poses.csv',index=False)
    data[data['rank']==1].to_csv(OUT/'tables/top1_per_seed.csv',index=False)
    print('Completed all 30 docking searches',flush=True)

if __name__=='__main__':main()
