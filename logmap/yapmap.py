from .imports import *

def in_jupyter(): return sys.argv[-1].endswith('json')

def get_tqdm(iterable,*args,**kwargs):
    return tqdm(iterable, **kwargs)

def pmap_iter(
        func, 
        objs, 
        args=[], 
        kwargs={}, 
        lim=None,
        num_proc=DEFAULT_NUM_PROC, 
        use_threads=False, 
        progress=True, 
        progress_pos=0,
        desc=None,
        shuffle=False,
        context=CONTEXT, 
        **y):
    """
    Yields results of func(obj) for each obj in objs
    Uses multiprocessing.Pool(num_proc) for parallelism.
    If use_threads, use ThreadPool instead of Pool.
    Results in any order.
    """

    # lim?
    if shuffle: random.shuffle(objs)
    if lim: objs = objs[:lim]

    # check num proc
    num_cpu = mp.cpu_count()
    if num_proc>num_cpu: num_proc=num_cpu
    if num_proc>len(objs): num_proc=len(objs)

    # if parallel
    if not desc: desc=f'Mapping {func.__name__}()'
    if desc and num_cpu>1: desc=f'{desc} [x{num_proc}]'
    if num_proc>1 and len(objs)>1:

        # real objects
        objects = [(func,obj,args,kwargs) for obj in objs]

        # create pool
        #pool=mp.Pool(num_proc) if not use_threads else mp.pool.ThreadPool(num_proc)
        with mp.get_context(context).Pool(num_proc) as pool:
            # yield iter
            iterr = pool.imap(_pmap_do, objects)

            for res in get_tqdm(iterr,total=len(objects),desc=desc,position=progress_pos) if progress else iterr:
                yield res
    else:
        # yield
        for obj in (tqdm(objs,desc=desc,position=progress_pos) if progress else objs):
            yield func(obj,*args,**kwargs)

def _pmap_do(inp):
    func,obj,args,kwargs = inp
    return func(obj,*args,**kwargs)

def pmap(*x,**y):
    """
    Non iterator version of pmap_iter
    """
    # return as list
    return list(pmap_iter(*x,**y))

def pmap_run(*x,**y):
    for obj in pmap_iter(*x,**y): pass



