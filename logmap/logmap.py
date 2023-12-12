from .imports import *

COLORS = {
    'default':'\033[0;39m',
    'light-blue':'\033[0;34m',
    'light-cyan':'\033[0;36m',
    'light-yellow':'\033[0;33m',
    'light-magenta':'\033[0;35m'
}



class logmap:
    """A class for monitoring and logging the duration of tasks.

    Attributes:
        started (float): The timestamp when the task started.
        ended (float): The timestamp when the task ended.
        level (str): The logging level for the task. Default is 'DEBUG'.
        log (Logger): The logger object for logging the task status.
        task_name (str): The name of the task being monitored.
    """
    def __init__(self, name='running task', level='DEBUG', min_seconds_logworthy=None, precision=1):
        global LOGWATCH_ID
        LOGWATCH_ID+=1
        self.id = LOGWATCH_ID
        self.started = None
        self.ended = None
        self.level=level.upper()
        self.task_name = name
        self.min_seconds_logworthy = min_seconds_logworthy
        self.vertical_char = '￨'
        self.last_lap = None
        self.level2color = {
            'TRACE':'\033[0;36m',
            'DEBUG':'\033[1;34m',
            'WARNING':'\033[1;33m',
            'ERROR':'\033[1;31m'
        }
        self.pbar = None
        self.num_proc=None
        self.precision = precision

    def log(self, msg, pref=None, inner_pref=True,level=None):
        if not msg: return
        if self.pbar is None:
            logfunc = getattr(logger,(self.level if not level else level).lower())
            logfunc(f'{(self.inner_pref if inner_pref else self.pref) if pref is None else pref}{msg}')
        else:
            if self.num_proc and self.num_proc>1: msg=f'{msg} [{self.num_proc}x]'
            self.set_progress_desc(msg)

    def iter_progress(self, iterator, desc='iterating', pref=None, position=0, total=None, **kwargs):
        # first arg is for percentage
        # 2nd one is for the bar
        # 3rd one is for the end of the line 
        read_bar_format = "%s{l_bar}%s{bar}%s{r_bar}" % (
                self.level2color[self.level], COLORS['light-cyan'], COLORS['light-cyan']
            )
        desc=f'{self.inner_pref if pref is None else pref}{desc if desc is not None else "iterating"}'
        self.pbar = tqdm(iterator,desc=desc,position=position,total=total,bar_format=read_bar_format,**kwargs)
        yield from self.pbar
        self.pbar.close()
        self.pbar = None
    
    def imap(
            self, 
            func, 
            objs, 
            args=[], 
            kwargs={}, 
            lim=None,
            num_proc=1, 
            desc=None,
            shuffle=None,
            context=CONTEXT,
            **pmap_kwargs):
        if desc is None: desc=f'mapping {func.__name__} to {len(objs)} objects'
        if num_proc>1: desc=f'{desc} [{num_proc}x]'
        self.num_proc=num_proc
        iterr = pmap_iter(
            func,
            objs,
            args=args,
            kwargs=kwargs,
            lim=lim,
            num_proc=num_proc,
            desc=None,
            shuffle=shuffle,
            context=context,
            progress=False,
            **pmap_kwargs
        )
        yield from self.iter_progress(
            iterr,
            desc=desc,
            total=len(objs)
        )

    def map(self, *args, **kwargs):
        return list(self.imap(*args, **kwargs))
    
    def run(self, *args, **kwargs):
        deque(self.imap(*args, **kwargs), maxlen=0)

    def nap(self):
        naptime = round(random.random(),self.precision)
        self.log(f'napping for {naptime} seconds')
        time.sleep(naptime)
        return naptime
    
    def set_progress_desc(self, desc,pref=None,**kwargs):
        if desc:
            desc=f'{self.inner_pref if pref is None else pref}{desc if desc is not None else ""}'
            self.pbar.set_description(desc,**kwargs)

    @property
    def tdesc(self): 
        """Returns the formatted timespan of the duration.
        
        Returns:
            str: The formatted timespan of the duration.
        
        Examples:
            >>> t = tdesc(self)
            >>> print(t)
            '2 hours 30 minutes'
        """
        return format_timespan(self.duration)
    
    def lap(self):
        self.last_lap = time.time()
    
    @property
    def lap_duration(self):
        return time.time() - self.last_lap if self.last_lap else 0
    
    @property
    def lap_tdesc(self):
        return format_timespan(self.lap_duration)
    

    @property
    def duration(self): 
        """Calculates the duration of an event.
        
        Returns:
            float: The duration of the event in seconds.
        """
        return round((self.ended if self.ended else time.time()) - self.started,self.precision)
    
    @cached_property
    def pref(self):
        return f"{self.vertical_char} " * (self.num-1)
    @cached_property
    def inner_pref(self):
        return f"{self.vertical_char} " * (self.num)
    

    @property
    def desc(self): 
        """Returns a description of the task.
        
        If the task has both a start time and an end time, it returns a string
        indicating the task name and the time it took to complete the task.
        
        If the task is currently running, it returns a string indicating that
        the task is still running.
        
        Returns:
            str: A description of the task.
        """
        if self.started is None or self.ended is None:
            return f'{self.task_name}'.strip()
        else:
            return f'⎿ {self.tdesc}'.strip()
        
    def __enter__(self):        
        """Context manager method that is called when entering a 'with' statement.
        
        This method logs the description of the context manager and starts the timer.
        
        Examples:
            with Logwatch():
                # code to be executed within the context manager
        """
        global NUM_LOGWATCHES
        NUM_LOGWATCHES+=1
        self.num = NUM_LOGWATCHES
        self.log(self.desc, inner_pref=False)
        self.started = self.last_lap = time.time()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """
        Logs the resulting time.
        """ 
        global NUM_LOGWATCHES, LOGWATCH_ID

        if exc_type:
            LOGWATCH_ID=0
            NUM_LOGWATCHES=0
            # logger.error(f'{exc_type.__name__} {exc_value}')
            self.log(f'{exc_type.__name__} {exc_value}', level='error')
        else:
            NUM_LOGWATCHES-=1
            self.ended = time.time()
            if not self.min_seconds_logworthy or self.duration>=self.min_seconds_logworthy:
                # if self.tdesc!='0 seconds':
                self.log(self.desc, inner_pref=False)
            if NUM_LOGWATCHES==0: LOGWATCH_ID=0

