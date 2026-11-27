import json,glob,os,numpy as np,pandas as pd
D='data/'
START='2023-09-01'; IS_END='2025-12-31'; OOS_START='2026-01-01'
def load(univ=40):
    c=pd.read_pickle(D+'c.pkl'); h=pd.read_pickle(D+'h.pkl'); l=pd.read_pickle(D+'l.pkl')
    n=pd.read_pickle(D+'n.pkl'); qv=pd.read_pickle(D+'qv.pkl')
    # drop last (partial) day
    X=build_X(c.iloc[:-1],h.iloc[:-1],l.iloc[:-1],n.iloc[:-1],qv.iloc[:-1],univ)
    c=X['c']
    # funding: daily sum by UTC day
    F={}
    for f in glob.glob(D+'funding/*.json'):
        d=json.load(open(f))
        if not d: continue
        s=pd.DataFrame(d); s['t']=pd.to_datetime(s['time'],unit='ms').dt.floor('D')
        F[os.path.basename(f)[:-5]]=s.groupby('t')['fundingRate'].apply(lambda x:x.astype(float).sum())
    fund=pd.DataFrame(F).reindex(index=c.index,columns=c.columns)
    med=fund.median(axis=1)
    has=fund.notna()
    fund=fund.apply(lambda col: col.fillna(med)).where(c.notna(),0).fillna(0)
    X.update(fund=fund,fund_has=has)
    return X

def build_X(c,h,l,n,qv,univ=40):
    valid=(n>0)
    c=c.where(valid); h=h.where(valid); l=l.where(valid)
    adv=qv.where(qv>0).rolling(30,min_periods=20).mean()
    elig=adv.rank(axis=1,ascending=False)<=univ
    ret=c.pct_change(fill_method=None)
    tr=pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()]).groupby(level=0).max()
    atr=tr.rolling(14,min_periods=10).mean()
    vol=ret.ewm(span=30,min_periods=20).std()*np.sqrt(365)
    return dict(c=c,h=h,l=l,ret=ret,elig=elig,atr=atr,vol=vol)

# ---------- signals: return matrix in [-1,1] known at close t ----------
def sig_tsmom(X,L): return np.sign(X['c']/X['c'].shift(L)-1)
def sig_ema(X,f,s):
    c=X['c']; return np.sign(c.ewm(span=f,min_periods=f).mean()-c.ewm(span=s,min_periods=s).mean())
def sig_donchian(X,N):
    c=X['c']; hi=c.rolling(N,min_periods=N).max().shift(); lo=c.rolling(N,min_periods=N).min().shift()
    xh=c.rolling(N//2).max().shift(); xl=c.rolling(N//2).min().shift()
    C=c.values; H=hi.values; Lo=lo.values; XH=xh.values; XL=xl.values; O=np.zeros(C.shape)
    st=np.zeros(C.shape[1])
    for t in range(C.shape[0]):
        x=C[t]
        up=x>H[t]; dn=x<Lo[t]
        st=np.where(np.isnan(x),0,st)
        st=np.where((st>0)&(x<XL[t]),0,st); st=np.where((st<0)&(x>XH[t]),0,st)
        st=np.where(up,1,np.where(dn,-1,st))
        O[t]=st
    return pd.DataFrame(O,index=c.index,columns=c.columns)
def sig_ensemble(X,longs=(7,14,28,56,84),emas=((8,32),(16,64),(32,128)),dons=(20,55)):
    parts=[sig_tsmom(X,L) for L in longs]+[sig_ema(X,f,s) for f,s in emas]+[sig_donchian(X,N) for N in dons]
    s=sum(p.fillna(0) for p in parts)/len(parts)
    return s.where(X['c'].notna())
def sig_xs(X,L,frac=0.2,skip=1,rebal=7,long_only=False):
    c=X['c']; r=(c.shift(skip)/c.shift(L+skip)-1).where(X['elig'])
    rk=r.rank(axis=1,pct=True)
    s=pd.DataFrame(0.0,index=c.index,columns=c.columns)
    s[rk>=1-frac]=1.0
    if not long_only: s[rk<=frac]=-1.0
    s=s.where(r.notna(),0)
    idx=np.arange(len(s))%rebal==0
    s=s.where(pd.Series(idx,index=s.index),np.nan).ffill()
    return s
def sig_dual(X,L,K=5,rebal=7):
    c=X['c']; r=(c/c.shift(L)-1).where(X['elig'])
    rk=r.rank(axis=1,ascending=False)
    s=((rk<=K)&(r>0)).astype(float)
    idx=np.arange(len(s))%rebal==0
    return s.where(pd.Series(idx,index=s.index),np.nan).ffill()

# ---------- ATR trailing stop applied to a signal ----------
def apply_stop(X,s,k,levels=False):
    if not k: return s
    C=X['c'].values; A=X['atr'].values; S=s.fillna(0).values.copy()
    out=np.zeros_like(S); n=S.shape[1]
    d=np.zeros(n); ext=np.full(n,np.nan); stopped=np.zeros(n,bool); last=np.zeros(n); LV=np.full(S.shape,np.nan)
    for t in range(S.shape[0]):
        sd=np.sign(S[t]); x=C[t]
        # signal direction change resets stop
        chg=sd!=last; stopped=np.where(chg,False,stopped); last=sd
        newpos=chg&(sd!=0)
        ext=np.where(newpos,x,ext)
        ext=np.where(sd>0,np.fmax(ext,x),np.where(sd<0,np.fmin(ext,x),np.nan))
        hit=((sd>0)&(x<ext-k*A[t]))|((sd<0)&(x>ext+k*A[t]))
        stopped=stopped|np.nan_to_num(hit).astype(bool)
        out[t]=np.where(stopped,0,S[t])
        LV[t]=np.where(sd>0,ext-k*A[t],np.where(sd<0,ext+k*A[t],np.nan))
    o=pd.DataFrame(out,index=s.index,columns=s.columns)
    if levels: return o,pd.DataFrame(LV,index=s.index,columns=s.columns)
    return o

# ---------- portfolio simulation ----------
def target_weights(X,s,asset_vt=True,port_vt=0.25,max_lev=2.0,pos_cap=0.25,long_only=False,exclude=None,norm='n'):
    ret=X['ret'].fillna(0); elig=X['elig']
    s=s.where(elig,0).fillna(0)
    if long_only: s=s.clip(lower=0)
    if exclude: s=s.copy(); s[exclude]=0
    if asset_vt:
        raw=s*(1/X['vol']).clip(upper=1/0.2).fillna(0)  # inverse vol, vol floor 20%
    else: raw=s.copy()
    N=(s!=0).sum(axis=1).replace(0,np.nan) if norm=='n' else elig.sum(axis=1)
    raw=raw.div(elig.sum(axis=1).replace(0,np.nan),axis=0).fillna(0)
    # portfolio vol scaling based on trailing realized vol of the unscaled book (lagged)
    pr=(raw.shift()*ret).sum(axis=1)
    rv=pr.rolling(60,min_periods=20).std()*np.sqrt(365)
    scale=(port_vt/rv).replace([np.inf],np.nan).fillna(0) if port_vt else pd.Series(1.0,index=raw.index)
    tgt=raw.mul(scale,axis=0)
    tgt=tgt.clip(-pos_cap,pos_cap)
    g=tgt.abs().sum(axis=1); tgt=tgt.mul(np.minimum(1,max_lev/g.replace(0,np.nan)).fillna(1),axis=0)
    return tgt

def simulate(X,s,asset_vt=True,port_vt=0.25,max_lev=2.0,pos_cap=0.25,dd_brake=None,
             fee=0.00045,slip=0.0003,band=0.1,long_only=False,exclude=None,start=START,end=None,norm='n',record_w=False):
    ret=X['ret'].fillna(0); fund=X['fund']
    tgt=target_weights(X,s,asset_vt,port_vt,max_lev,pos_cap,long_only,exclude,norm)
    idx=tgt.loc[start:end].index
    T=tgt.loc[idx].values; R=ret.loc[idx].values; FU=fund.loc[idx].values
    w=np.zeros(T.shape[1]); eq=1.0; peak=1.0
    E=[];G=[];TO=[];CF=[];FF=[]; ATT=np.zeros(T.shape[1]); WR=[]
    for t in range(len(idx)):
        # pnl over day t of weights held from close t-1
        pnl=np.dot(w,R[t]); fpay=-np.dot(w,FU[t]); ATT+=eq*(w*R[t]-w*FU[t])
        eq_new=eq*(1+pnl+fpay)
        # drift weights
        w=w*(1+R[t])/(1+pnl+fpay) if (1+pnl+fpay)>0 else w*0
        eq=max(eq_new,0); peak=max(peak,eq)
        tg=T[t].copy()
        if dd_brake:
            dd=1-eq/peak
            m=1.0
            for lvl,mult in dd_brake:
                if dd>=lvl: m=mult
            tg*=m
        # no-trade band
        diff=tg-w
        trade=np.abs(diff)>band*np.maximum(np.abs(tg),0.01)
        nw=np.where(trade,tg,w)
        to=np.abs(nw-w).sum(); cost=to*(fee+slip)
        eq*=(1-cost); w=nw
        if record_w: WR.append(w.copy())
        E.append(eq);G.append(np.abs(w).sum());TO.append(to);CF.append(cost);FF.append(fpay)
    res=pd.DataFrame(dict(eq=E,gross=G,turnover=TO,cost=CF,funding=FF),index=idx)
    res.attrs['att']=pd.Series(ATT,index=tgt.columns)
    if record_w: res.attrs['W']=pd.DataFrame(WR,index=idx,columns=tgt.columns)
    return res

def stats(res,a=None,b=None):
    dra=res['eq'].pct_change(); dra.iloc[0]=res['eq'].iloc[0]-1
    dr=dra.loc[a:b]; r=res.loc[a:b]
    if len(r)<10: return {}
    eq=(1+dr).cumprod()
    yrs=len(r)/365
    tot=eq.iloc[-1]-1
    cagr=eq.iloc[-1]**(1/yrs)-1 if eq.iloc[-1]>0 else -1
    mdd=(1-eq/eq.cummax().clip(lower=1)).max()
    sh=dr.mean()/dr.std()*np.sqrt(365) if dr.std()>0 else 0
    return dict(ret=tot,cagr=cagr,mdd=mdd,sharpe=sh,cost=r['cost'].sum(),funding=r['funding'].sum(),
                turnover=r['turnover'].sum()/yrs,gross=r['gross'].mean())
