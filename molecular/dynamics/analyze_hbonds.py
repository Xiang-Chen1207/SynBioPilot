"""Direct protein–ligand H bonds on all saved frames; four selected 200 ns runs.

Main definition: explicit conventional N/O donors/acceptors, D–A <=3.5 Å,
D–H–A >=150 degrees, periodic minimum-image geometry. A second SFN-only
category records N04/S02 geometric contacts, separately from main H bonds.
"""
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
import csv, gzip
import numpy as np
import pandas as pd
import MDAnalysis as mda
from MDAnalysis.lib.distances import distance_array, calc_angles

import os
ROOT=Path(os.environ['SYNBIOPILOT_MOLECULAR_ASSETS']).resolve()
OUT=Path(os.environ['SYNBIOPILOT_MOLECULAR_OUTPUT']).resolve()
OUT.mkdir(parents=True, exist_ok=True)
TABLE=OUT/'tables'
TABLE.mkdir(parents=True, exist_ok=True)
GROUPS={'SAHA':['05','06'],'SFN pose A':['08','09']}
MODELS={'SAHA':'hdac2_saha','SFN pose A':'hdac2_sfn_pose_a'}
WINDOWS=[('full_0_200',0,200),('20_200',20,200),('0_50',0,50),
         ('50_100',50,100),('100_150',100,150),('150_200',150,200)]

def donor_pairs(atoms):
    pairs=[]
    for a in atoms:
        if a.element in ('N','O'):
            pairs.extend((a.index,h.index) for h in a.bonded_atoms if h.element=='H')
    return np.array(pairs,dtype=int).reshape(-1,2)

def protein_acceptors(atoms):
    # Production histidine protonation determines the available ring nitrogen.
    return np.array([a.index for a in atoms if a.element=='O' or
        (a.resname=='HID' and a.name=='NE2') or
        (a.resname=='HIE' and a.name=='ND1')],dtype=int)

def analyze(item):
    group,run=item
    model=ROOT/'preparation/interaction_panel/models/zn_1264'/MODELS[group]
    traj=ROOT/'md/INTERACTION_PANEL_20261010/production_zn_1264_200ns/replicas'/run/'production/md.xtc'
    u=mda.Universe(str(model/'system.prmtop'),str(traj))
    protein=u.residues[:368].atoms
    ligand=u.residues[371].atoms
    ligand_names={a.name:a.index for a in ligand}
    pdh=donor_pairs(protein);ldh=donor_pairs(ligand)
    pa=protein_acceptors(protein)
    la=np.array([ligand_names[n] for n in (['O01','O02','O12'] if group=='SAHA' else ['O03'])])
    directions=[('main',pdh,la),('main',ldh,pa)]
    if group=='SFN pose A':
        directions.append(('expanded_SFN_contacts',pdh,np.array([ligand_names[n] for n in ['N04','S02']])))
    atoms=[]
    for category,dh,acceptors in directions:
        for role,ids in [('donor',np.unique(dh[:,0])),('hydrogen',np.unique(dh[:,1])),('acceptor',acceptors)]:
            for idx in ids:
                a=u.atoms[idx]
                atoms.append(dict(condition=group,run=run,category=category,role=role,atom_index_0based=int(idx),
                    resname=a.resname,topology_resid=int(a.resid),protein_resid=int(a.resid)+11 if idx<5840 else '',atom_name=a.name))
    pd.DataFrame(atoms).drop_duplicates().to_csv(TABLE/f'Hbond_atom_selection_{run}.csv',index=False)
    event_path=TABLE/f'Hbond_events_{run}.csv.gz'
    fields=['frame','time_ns','category','donor_index_0based','hydrogen_index_0based','acceptor_index_0based',
            'donor_label','hydrogen_name','acceptor_label','protein_resid','protein_resname','ligand_atom',
            'direction','donor_acceptor_A','D_H_A_deg']
    records=[]
    def label(idx):
        a=u.atoms[idx]
        return f'{a.resname}{a.resid+11}:{a.name}' if idx<5840 else f'{a.resname}:{a.name}'
    with gzip.open(event_path,'wt',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for ts in u.trajectory:
            pos=u.atoms.positions
            counts={'main':set(),'expanded_SFN_contacts':set()}
            triplets={'main':0,'expanded_SFN_contacts':0}
            for category,dh,acceptors in directions:
                if not len(dh) or not len(acceptors):continue
                ds=distance_array(pos[dh[:,0]],pos[acceptors],box=ts.dimensions)
                di,ai=np.where((ds<=3.5)&(ds>0))
                if not len(di):continue
                ang=np.rad2deg(calc_angles(pos[dh[di,0]],pos[dh[di,1]],pos[acceptors[ai]],box=ts.dimensions))
                for k in np.flatnonzero(ang>=150.):
                    d,h=map(int,dh[di[k]]);a=int(acceptors[ai[k]])
                    pro_idx=d if d<5840 else a;lig_idx=a if d<5840 else d
                    pro=u.atoms[pro_idx]
                    counts[category].add((d,a));triplets[category]+=1
                    writer.writerow(dict(frame=ts.frame,time_ns=ts.time/1000,category=category,
                        donor_index_0based=d,hydrogen_index_0based=h,acceptor_index_0based=a,
                        donor_label=label(d),hydrogen_name=u.atoms[h].name,acceptor_label=label(a),
                        protein_resid=int(pro.resid)+11,protein_resname=pro.resname,ligand_atom=u.atoms[lig_idx].name,
                        direction='protein_to_ligand' if d<5840 else 'ligand_to_protein',
                        donor_acceptor_A=float(ds[di[k],ai[k]]),D_H_A_deg=float(ang[k])))
            records.append(dict(condition=group,run=run,frame=ts.frame,time_ns=ts.time/1000,
                n_hbonds=len(counts['main']),n_DHA_triplets=triplets['main'],
                n_expanded_SFN_contacts=len(counts['expanded_SFN_contacts'])))
            if (ts.frame+1)%5000==0:print(f'{run}: {ts.frame+1}/{len(u.trajectory)} frames, {ts.time/1000:.2f} ns',flush=True)
    data=pd.DataFrame(records)
    data.to_csv(TABLE/f'Hbond_timeseries_{run}.csv',index=False)
    events=pd.read_csv(event_path)
    summaries=[];pairs=[];residues=[]
    for window,lo,hi in WINDOWS:
        mask=(data.time_ns>=lo-1e-6)&(data.time_ns<=hi+1e-6) if window=='20_200' else (data.time_ns>lo+1e-6)&(data.time_ns<=hi+1e-6)
        d=data[mask];e=events[events.frame.isin(d.frame)]
        for category,col in [('main','n_hbonds'),('expanded_SFN_contacts','n_expanded_SFN_contacts')]:
            if category!='main' and group=='SAHA':continue
            v=d[col];ec=e[e.category==category]
            summaries.append(dict(condition=group,run=run,category=category,window=window,frames=len(d),
                first_time_ns=d.time_ns.min(),last_time_ns=d.time_ns.max(),mean_count=v.mean(),
                temporal_SD_count=v.std(ddof=1),any_contact_fraction=(v>0).mean(),max_count=v.max()))
            cols=['donor_index_0based','acceptor_index_0based','donor_label','acceptor_label','protein_resid','protein_resname','ligand_atom','direction']
            for keys,part in ec.groupby(cols,sort=False):
                pairs.append(dict(condition=group,run=run,category=category,window=window,
                    **dict(zip(cols,keys)),occupied_frames=part.frame.nunique(),total_frames=len(d),
                    occupancy_fraction=part.frame.nunique()/len(d),mean_D_A_A=part.donor_acceptor_A.mean(),mean_D_H_A_deg=part.D_H_A_deg.mean()))
            for (resid,resname),part in ec.groupby(['protein_resid','protein_resname']):
                residues.append(dict(condition=group,run=run,category=category,window=window,protein_resid=resid,
                    protein_resname=resname,occupied_frames=part.frame.nunique(),total_frames=len(d),
                    occupancy_fraction=part.frame.nunique()/len(d)))
    for name,rows in [('windows',summaries),('pairs',pairs),('residues',residues)]:
        pd.DataFrame(rows).to_csv(TABLE/f'Hbond_{name}_{run}.csv',index=False)
    print(f'{run} done; mean={data.n_hbonds.mean():.4f}, any={(data.n_hbonds>0).mean():.4f}',flush=True)
    return run

def main():
    TABLE.mkdir(parents=True,exist_ok=True)
    items=[(g,r) for g,runs in GROUPS.items() for r in runs]
    with ProcessPoolExecutor(max_workers=4) as pool:list(pool.map(analyze,items))
    for name in ['timeseries','windows','pairs','residues']:
        parts=[pd.read_csv(TABLE/f'Hbond_{name}_{r}.csv',dtype={'run':str}) for _,r in items]
        pd.concat(parts,ignore_index=True).to_csv(TABLE/f'Hbond_{name}.csv',index=False)
    w=pd.read_csv(TABLE/'Hbond_windows.csv',dtype={'run':str})
    group=w.groupby(['condition','category','window']).agg(n_trajectories=('run','nunique'),
        mean_count=('mean_count','mean'),between_trajectory_SD_count=('mean_count','std'),
        mean_any_contact_fraction=('any_contact_fraction','mean'),
        between_trajectory_SD_fraction=('any_contact_fraction','std')).reset_index()
    group.to_csv(TABLE/'Hbond_group_summary.csv',index=False)

if __name__=='__main__':main()
