"""Single-trajectory GBSA endpoint scores for the four selected trajectories.

Amber GB supports the 12-6 baseline here; the noncancelling ligand-receptor
12-6-4 C4 cross term is evaluated explicitly from the production topology.
This hybrid endpoint score excludes entropy and is not a binding free energy.
Run on a provisioned compute node with the documented input assets.
"""
from pathlib import Path
import os,json,re,subprocess,copy,sys,zipfile
from concurrent.futures import ProcessPoolExecutor,as_completed
import numpy as np
import pandas as pd
import parmed as pmd
from parmed.amber import NetCDFTraj
from parmed.tools.actions import changeRadii
import MDAnalysis as mda
from MDAnalysis.lib.distances import minimize_vectors,distance_array

import os
ROOT=Path(os.environ['SYNBIOPILOT_MOLECULAR_ASSETS']).resolve()
OUT=Path(os.environ['SYNBIOPILOT_MOLECULAR_OUTPUT']).resolve()
OUT.mkdir(parents=True, exist_ok=True)
BASE=ROOT/'md/INTERACTION_PANEL_20261010/production_zn_1264_200ns'
RUNS=[('05','hdac2_saha'),('06','hdac2_saha'),
      ('08','hdac2_sfn_pose_a'),('09','hdac2_sfn_pose_a')]
MDIN='''Single-trajectory GBSA endpoint evaluation (C4 cross term added separately)
&cntrl
 imin=5, maxcyc=1, ncyc=0, ntb=0, cut=999.0, nsnb=99999,
 igb=5, gbsa=1, surften=0.005, saltcon=0.15,
 intdiel=1.0, extdiel=78.5, offset=0.09,
 ntc=1, ntf=1, lj1264=0, ioutfm=1,
/
'''

def parse_energies(path):
    """Read the standard Sander single-point energy blocks, in frame order."""
    blocks=[];row=None
    pattern=r'(BOND|ANGLE|DIHED|VDWAALS|1-4 VDW|1-4 EEL|EEL|EGB|ESURF)\s*=\s*([-+]?\d*\.?\d+(?:[EeDd][-+]?\d+)?)'
    for line in path.read_text().splitlines():
        if re.search(r'\bBOND\s*=',line):
            if row is not None:blocks.append(row)
            row={}
        if row is not None:
            for name,value in re.findall(pattern,line):
                key=name.strip()
                if key in ['BOND','ANGLE','DIHED','VDWAALS','EEL','EGB','ESURF','1-4 VDW','1-4 EEL']:
                    row[key]=float(value.replace('D','E'))
    if row is not None:blocks.append(row)
    return pd.DataFrame(blocks)

def analyze(item):
    run,condition=item;dest=OUT/'energy'/run;dest.mkdir(parents=True,exist_ok=True)
    model=ROOT/'preparation/interaction_panel/models/zn_1264'/condition
    parm=pmd.load_file(str(model/'system.prmtop'))
    u=mda.Universe(str(model/'system.prmtop'),str(BASE/'replicas'/run/'production/md.xtc'))
    protein=u.residues[:368].atoms
    receptor=u.residues[:371].atoms
    ligand=u.residues[371].atoms
    nrec=len(receptor);ncomplex=nrec+len(ligand)
    inp=mda.Universe(str(model/'system.prmtop'),str(model/'system.gro'))
    anchor_positions=inp.atoms[protein.indices].positions
    ion_indices=u.residues[368:371].atoms.indices
    ion_anchor=distance_array(inp.atoms[ion_indices].positions,anchor_positions,box=inp.dimensions).argmin(axis=1)
    pocket_resids=[32,33,34,103,104,145,146,154,155,181,183,210,265,269,276,306,308]
    pocket_ca=[a.index for a in protein if a.name=='CA' and a.resid+11 in pocket_resids]
    atypes=np.array(parm.parm_data['ATOM_TYPE_INDEX'],dtype=int)-1
    nbindex=np.array(parm.parm_data['NONBONDED_PARM_INDEX'],dtype=int).reshape(parm.ptr('ntypes'),-1)-1
    coeff=np.array(parm.parm_data['LENNARD_JONES_CCOEF'])
    cross_indices=nbindex[atypes[receptor.indices,None],atypes[ligand.indices][None,:]]
    c4mat=np.where(cross_indices>=0,coeff[np.maximum(cross_indices,0)],0)
    ci,cj=np.where(c4mat!=0);c4coef=c4mat[ci,cj]
    # Three dry topologies retain identical 12-6, charge and bonded parameters.
    # Only continuum-solvent radii are assigned for the explicitly stated OBC2 calculation.
    dry={}
    for component,mask in [('complex',':1-372'),('receptor',':1-371'),('ligand',':372')]:
        p=copy.deepcopy(parm);p.strip('!'+mask);p.box=None
        changeRadii(p,'mbondi2').execute();p.save(str(dest/f'{component}.prmtop'),overwrite=True)
        dry[component]=p
    del parm,inp
    frames=[];times=[];cross=[]
    start_time=float(u.trajectory[0].time)
    # Saved trajectory starts at 10 ps; select 1,2,...,200 ns without changing coordinates.
    selected=[i for i in range(len(u.trajectory)) if abs((start_time+i*10)/1000-round((start_time+i*10)/1000))<1e-5 and (start_time+i*10)>=1000-1e-4]
    for idx in selected:
        ts=u.trajectory[idx];box=ts.dimensions
        p=protein.unwrap(compound='fragments',reference=None,inplace=False).astype(float)
        ions=np.array([p[a]+minimize_vectors((u.atoms[ii].position-protein[a].position)[None,:],box)[0]
                       for ii,a in zip(ion_indices,ion_anchor)])
        lp=ligand.unwrap(compound='fragments',reference=None,inplace=False).astype(float)
        center=p[pocket_ca].mean(0)
        shift=lp.mean(0)-center;lp+=minimize_vectors(shift[None,:],box)[0]-shift
        xyz=np.concatenate([p,ions,lp])
        distances=np.linalg.norm(xyz[ci]-xyz[nrec+cj],axis=1)
        ec4=float(np.sum(-c4coef/distances**4))
        frames.append(xyz);times.append(float(ts.time/1000));cross.append(ec4)
    coords=np.array(frames);times=np.array(times)
    (dest/'gbsa.in').write_text(MDIN)
    parts={}
    for component,sl in [('complex',slice(None)),('receptor',slice(0,nrec)),('ligand',slice(nrec,None))]:
        xyz=coords[:,sl,:];p=dry[component];p.coordinates=xyz[0]
        p.save(str(dest/f'{component}.rst7'),overwrite=True)
        nc=NetCDFTraj.open_new(str(dest/f'{component}.nc'),natom=xyz.shape[1],box=False,crds=True)
        for time,x in zip(times,xyz):nc.add_coordinates(x);nc.add_time(time*1000)
        nc.close()
        cmd=['sander','-O','-i','gbsa.in','-p',f'{component}.prmtop','-c',f'{component}.rst7',
             '-y',f'{component}.nc','-o',f'{component}.mdout','-r',f'{component}_out.rst7','-inf',f'{component}.mdinfo']
        print(f'{run}: {component} evaluating {len(times)} frames',flush=True)
        with (dest/f'{component}.stdout.log').open('w') as log:
            subprocess.run(cmd,cwd=dest,stdout=log,stderr=subprocess.STDOUT,check=True)
        result=parse_energies(dest/f'{component}.mdout')
        if len(result)!=len(times):raise RuntimeError(f'{run}/{component}: expected {len(times)} energy blocks, got {len(result)}')
        if not np.isfinite(result.values).all():raise RuntimeError(f'{run}/{component}: nonfinite energies')
        result.insert(0,'time_ns',times);result.to_csv(dest/f'{component}_energies.csv',index=False)
        parts[component]=result.drop(columns='time_ns')
    delta=parts['complex']-parts['receptor']-parts['ligand']
    delta.insert(0,'time_ns',times)
    energy_cols=[c for c in delta if c!='time_ns']
    delta['MMGBSA_12_6_no_entropy']=delta[energy_cols].sum(axis=1)
    delta['C4_cross']=cross
    delta['MMGBSA_plus_C4_no_entropy']=delta.MMGBSA_12_6_no_entropy+delta.C4_cross
    delta['run']=run;delta['condition']='SAHA' if condition=='hdac2_saha' else 'SFN pose A'
    delta.to_csv(dest/'endpoint_timeseries.csv',index=False)
    metadata={'run':run,'condition':condition,'frames':len(times),'time_ns':[float(times[0]),float(times[-1])],
              'sample_interval_ns':1,'receptor':'protein residues 1-368 plus native Zn/Ca/Na residues 369-371',
              'ligand':str(ligand.resnames[0]),'C4_cross_pairs':len(ci),
              'gb':'OBC2 igb=5, mbondi2, intdiel=1, extdiel=78.5, saltcon=0.15 M',
              'nonpolar':'LCPO gbsa=1, surften=0.005 kcal/mol/A^2, no offset',
              'C4':'explicit -sum C4_ij/r_ij^4 over ligand-receptor cross pairs from production topology; no PBC/cutoff in dry endpoint',
              'limitation':'Hybrid MM/GBSA + explicit C4 endpoint estimate; entropy, reorganization and standard-state terms are omitted. Metal-site accuracy has not been calibrated against a dedicated reference set.',
              'ions_GB_radii_A':{a.name:a.solvent_radius for a in dry['receptor'].atoms if a.atomic_number in [11,20,30]},
              'ions_GB_screen':{a.name:a.screen for a in dry['receptor'].atoms if a.atomic_number in [11,20,30]}}
    (dest/'method.json').write_text(json.dumps(metadata,indent=2))
    print(f'{run}: COMPLETE, endpoint mean {delta.MMGBSA_plus_C4_no_entropy.mean():.3f} kcal/mol',flush=True)
    return run

def main():
    with ProcessPoolExecutor(max_workers=6) as ex:
        futures=[ex.submit(analyze,item) for item in RUNS]
        for f in as_completed(futures):f.result()
    print('ALL_FOUR_ENDPOINTS_COMPLETE', flush=True)

if __name__=='__main__':main()
