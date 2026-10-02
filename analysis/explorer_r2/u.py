import statistics as st, math, bisect
def pct(xs,p):
    xs=sorted(xs); 
    if not xs: return float('nan')
    k=(len(xs)-1)*p/100; f=math.floor(k); c=min(f+1,len(xs)-1)
    return xs[f]+(xs[c]-xs[f])*(k-f)
def wmean(pairs):  # (w,x)
    W=sum(w for w,x in pairs); return sum(w*x for w,x in pairs)/W if W else float('nan')
def wpct(pairs,p):
    pairs=sorted(pairs,key=lambda a:a[1]); W=sum(w for w,x in pairs); c=0
    for w,x in pairs:
        c+=w
        if c>=W*p/100: return x
    return pairs[-1][1]
def sd(xs): return st.pstdev(xs) if len(xs)>1 else 0.0
def med(xs): return st.median(xs) if xs else float('nan')
