"""Requested four-trajectory comparison: RMSD, RMSF, Zn geometry and endpoint energy."""
from pathlib import Path
import os,json,shutil
ROOT=Path(os.environ['SYNBIOPILOT_MOLECULAR_ASSETS']).resolve()
OUT=Path(os.environ['SYNBIOPILOT_MOLECULAR_OUTPUT']).resolve()
OUT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault('MPLCONFIGDIR',str(OUT/'.cache/matplotlib'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np
import pandas as pd

SOURCE=ROOT/'results/publication_results_25runs_20261012'
GROUPS={'SAHA':['05','06'],'SFN pose A':['08','09']}
COLORS={'SAHA':'#0072B2','SFN pose A':'#D55E00'}
LS=['-','--',':']
FIG=OUT/'figures';TABLE=OUT/'tables'
FIG.mkdir(exist_ok=True);TABLE.mkdir(exist_ok=True)
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':9,'axes.labelsize':10,'xtick.labelsize':8,'ytick.labelsize':8,
                     'legend.fontsize':8,'axes.spines.top':False,'axes.spines.right':False,'pdf.fonttype':42,'savefig.dpi':300})
def save(fig,stem):
    for ext in ['png','pdf']:fig.savefig(FIG/f'{stem}.{ext}',bbox_inches='tight',pad_inches=.13)
    plt.close(fig);print(stem,flush=True)
def panel(ax,letter):ax.text(-.11,1.03,letter,transform=ax.transAxes,fontsize=12,fontweight='bold')
def frame(run):return pd.read_csv(SOURCE/'data/hdac2'/run/'metrics.csv')
def rawlines(ax,g,metric,scale=10):
    for i,run in enumerate(GROUPS[g]):
        d=frame(run);ax.plot(d.time_ns,d[metric]*scale,color=COLORS[g],ls=LS[i],lw=.7,alpha=.85,label=f'{g} {run}')
    ax.set_xlim(0,200);ax.legend(loc='lower center',bbox_to_anchor=(.5,1.02),ncol=2,fontsize=7,frameon=False)

def prepare_tables():
    for name in ['HDAC2_windows','HDAC2_RMSF','HDAC2_Zn_geometry']:
        d=pd.read_csv(SOURCE/'tables'/f'{name}.csv',dtype={'run':str})
        d=d[d.run.isin(sum(GROUPS.values(),[]))]
        d.to_csv(TABLE/f'{name}.csv',index=False)
    allrows=[]
    for g,runs in GROUPS.items():
        for i,run in enumerate(runs):
            d=frame(run)
            cols=['time_ns','protein_ca_rmsd_nm','ligand_heavy_rmsd_nm']+[c for c in d if c.startswith('zn_') and c.endswith('_nm')]
            p=d[cols].copy()
            for c in p:
                if c.endswith('_nm'):p[c]*=10
            p.columns=[c.replace('_nm','_A') for c in p]
            p.insert(0,'run',run);p.insert(1,'condition',g);p.insert(2,'replicate',i+1)
            p.to_csv(TABLE/f'timeseries_{run}.csv',index=False);allrows.append(p)
    pd.concat(allrows,ignore_index=True).to_csv(TABLE/'All_four_RMSD_Zn_timeseries.csv',index=False)

def rmsd():
    fig,axes=plt.subplots(2,2,figsize=(10,6.8),sharex=True,sharey='row',layout='constrained')
    for j,g in enumerate(GROUPS):
        for i,(metric,label) in enumerate([('protein_ca_rmsd_nm','Protein Cα RMSD (Å)'),('ligand_heavy_rmsd_nm','Ligand heavy-atom RMSD (Å)')]):
            ax=axes[i,j];rawlines(ax,g,metric);ax.set_ylabel(label);panel(ax,chr(97+i*2+j))
    for ax in axes[-1]:ax.set_xlabel('Time (ns)')
    save(fig,'Fig1_RMSD')

def rmsf():
    rf=pd.read_csv(TABLE/'HDAC2_RMSF.csv',dtype={'run':str}).query("window=='full_0_200'")
    fig=plt.figure(figsize=(10,7),layout='constrained');gs=fig.add_gridspec(2,2)
    top=fig.add_subplot(gs[0,:]);left=fig.add_subplot(gs[1,0],sharey=top);right=fig.add_subplot(gs[1,1],sharey=top)
    for g,ax in zip(GROUPS,[left,right]):
        pivot=rf[rf.condition==g].pivot(index='resid',columns='run',values='rmsf_A')
        y=pivot.mean(axis=1);sd=pivot.std(axis=1,ddof=1)
        top.plot(pivot.index,y,color=COLORS[g],lw=1,label=f'{g}: mean ± SD, n=2')
        top.fill_between(pivot.index,y-sd,y+sd,color=COLORS[g],alpha=.17,lw=0)
        for i,run in enumerate(GROUPS[g]):ax.plot(pivot.index,pivot[run],color=COLORS[g],lw=.8,ls=LS[i],label=f'{g} {run}')
        ax.legend(fontsize=7)
    for i,ax in enumerate([top,left,right]):
        ax.set(xlim=(12,379),xlabel='HDAC2 residue (4LXZ numbering)',ylabel='Cα RMSF (Å)');panel(ax,chr(97+i))
    top.legend(ncol=2);save(fig,'Fig2_RMSF')

def zinc():
    metrics=[('zn_ligand_min_donor_distance_nm','Zn–ligand N/O/S min. (Å)'),
             ('zn_native_residue_172_HIE_min_nm','Zn–His183 ND1 (Å)'),
             ('zn_native_residue_170_ASP_min_nm','Zn–Asp181 min. O (Å)'),
             ('zn_native_residue_258_ASP_min_nm','Zn–Asp269 min. O (Å)'),
             ('zn_water_min_oxygen_distance_nm','Zn–nearest water O (Å)')]
    fig,axes=plt.subplots(5,2,figsize=(10,12),sharex=True,sharey='row',layout='constrained')
    for j,g in enumerate(GROUPS):
        for i,(metric,label) in enumerate(metrics):
            ax=axes[i,j];rawlines(ax,g,metric);ax.set_ylabel(label);ax.axhline(3,color='black',ls='--',lw=.6);panel(ax,chr(97+i*2+j))
            if i:ax.get_legend().remove()
    for ax in axes[-1]:ax.set_xlabel('Time (ns)')
    save(fig,'Fig3_Zn_relative_distances')
    for kind,stem in [('ligand','FigS1_Zn_each_ligand_atom'),('native','FigS2_Zn_each_native_donor_atom')]:
        fig,axes=plt.subplots(2,2,figsize=(10,6.8),sharex=True,sharey=True,layout='constrained')
        for j,(g,runs) in enumerate(GROUPS.items()):
            for i,run in enumerate(runs):
                d=frame(run);ax=axes[i,j]
                if kind=='ligand':
                    cols=[c for c in d if c.startswith('zn_ligand_') and c.endswith('_nm') and 'min_donor' not in c]
                    labels=[c.split('_')[2] for c in cols]
                else:
                    cols=[c for c in d if c.startswith('zn_native_') and c.endswith('_nm') and 'residue' not in c]
                    labels=[f"{int(c.split('_')[2])+11}:{c.split('_')[4]}" for c in cols]
                for n,(col,label) in enumerate(zip(cols,labels)):
                    ax.plot(d.time_ns,d[col]*10,lw=.75,label=label,color=['#0072B2','#D55E00','#009E73','#CC79A7','#E69F00'][n])
                ax.axhline(3,color='black',ls='--',lw=.6)
                ax.set(ylabel='Zn–atom distance (Å)',xlim=(0,200));ax.legend(loc='lower center',bbox_to_anchor=(.5,1.02),ncol=5,fontsize=7,frameon=False)
                ax.text(.02,.95,f'{g} {run}',transform=ax.transAxes,va='top',fontsize=8,bbox={'facecolor':'white','edgecolor':'none','alpha':.8})
                panel(ax,chr(97+i*2+j))
        for ax in axes[-1]:ax.set_xlabel('Time (ns)')
        save(fig,stem)

def energy():
    dfs=[]
    for g,runs in GROUPS.items():
        for run in runs:
            p=OUT/'energy'/run/'endpoint_timeseries.csv'
            if not p.exists():return False
            dfs.append(pd.read_csv(p,dtype={'run':str}))
    df=pd.concat(dfs,ignore_index=True);df.to_csv(TABLE/'Endpoint_energy_timeseries.csv',index=False)
    metrics=['BOND','ANGLE','DIHED','VDWAALS','EEL','EGB','ESURF','1-4 VDW','1-4 EEL','C4_cross','MMGBSA_12_6_no_entropy','MMGBSA_plus_C4_no_entropy']
    rows=[]
    for window,lo,hi in [('1-200 ns',1,200),('20-200 ns',20,200),('1-50 ns',1,50),('51-100 ns',51,100),('101-150 ns',101,150),('151-200 ns',151,200)]:
        for (g,run),d in df[(df.time_ns>=lo-1e-6)&(df.time_ns<=hi+1e-6)].groupby(['condition','run'],sort=False):
            r={'condition':g,'run':run,'window':window,'frames':len(d)}
            for m in metrics:r[m]=d[m].mean()
            r['temporal_SD_endpoint']=d.MMGBSA_plus_C4_no_entropy.std(ddof=1);rows.append(r)
    summary=pd.DataFrame(rows);summary.to_csv(TABLE/'Endpoint_energy_windows.csv',index=False)
    main=summary[summary.window=='20-200 ns']
    fig,axes=plt.subplots(2,2,figsize=(10,7.3),layout='constrained')
    for j,g in enumerate(GROUPS):
        ax=axes[0,j]
        for i,run in enumerate(GROUPS[g]):
            d=df[df.run==run];ax.plot(d.time_ns,d.MMGBSA_plus_C4_no_entropy,color=COLORS[g],ls=LS[i],lw=.8,label=f'{g} {run}')
        ax.axvspan(0,20,color='gray',alpha=.1);ax.axhline(0,color='black',ls='--',lw=.6)
        ax.set(xlim=(0,200),xlabel='Time (ns)',ylabel='MM/GBSA + C4 score\n(no entropy; kcal mol⁻¹)');ax.legend(loc='lower center',bbox_to_anchor=(.5,1.02),ncol=2,fontsize=7,frameon=False);panel(ax,chr(97+j))
    lo=min(ax.get_ylim()[0] for ax in axes[0]);hi=max(ax.get_ylim()[1] for ax in axes[0])
    for ax in axes[0]:ax.set_ylim(lo,hi)
    ax=axes[1,0]
    for x,g in enumerate(GROUPS):
        p=main[main.condition==g];y=p.MMGBSA_plus_C4_no_entropy.values
        ax.errorbar(x,y.mean(),yerr=y.std(ddof=1),fmt='_',color=COLORS[g],lw=1.5,capsize=5,ms=20)
        xx=x+np.linspace(-.08,.08,len(y));ax.scatter(xx,y,color=COLORS[g],zorder=3)
        for j,(px,py,run) in enumerate(zip(xx,y,p.run)):
            ax.annotate(run,(px,py),xytext=(-3 if j==0 else 3,5),ha='right' if j==0 else 'left',textcoords='offset points',fontsize=8)
    ax.set(xticks=[0,1],xticklabels=list(GROUPS),xlim=(-.45,1.45),ylabel='20–200 ns endpoint score\n(no entropy; kcal mol⁻¹)');ax.margins(y=.2);panel(ax,'c')
    ax=axes[1,1];components=['VDWAALS','EEL','EGB','ESURF','C4_cross'];x=np.arange(len(components))
    for i,g in enumerate(GROUPS):
        p=main[main.condition==g]
        ax.bar(x+(i-.5)*.35,p[components].mean(),yerr=p[components].std(ddof=1),width=.35,color=COLORS[g],label=g,capsize=3)
    ax.axhline(0,color='black',lw=.6);ax.set(xticks=x,xticklabels=['LJ 12–6','Coulomb','GB polar','SA','C4'],ylabel='20–200 ns component\n(kcal mol⁻¹)');ax.legend();panel(ax,'d')
    save(fig,'Fig4_Endpoint_binding_energy');return True

def excel():
    with pd.ExcelWriter(TABLE/'SFN_A_vs_SAHA_Source_Data.xlsx',engine='openpyxl') as writer:
        for name in ['HDAC2_windows','HDAC2_RMSF','HDAC2_Zn_geometry','All_four_RMSD_Zn_timeseries','Endpoint_energy_timeseries','Endpoint_energy_windows']:
            p=TABLE/f'{name}.csv'
            if p.exists():pd.read_csv(p,dtype={'run':str}).to_excel(writer,sheet_name=name[:31],index=False)

if __name__=='__main__':
    prepare_tables();rmsd();rmsf();zinc();done=energy();excel();print('Energy included:',done)
