import sys
import random
import time
from functools import cached_property
from loguru import logger
from humanfriendly import format_timespan
import multiprocess as mp
from collections import deque
from tqdm import tqdm
from contextlib import contextmanager

LOG_FORMAT = '<level>{message}</level><cyan> @ {time:YYYY-MM-DD HH:mm:ss,SSS}</cyan>'
NUM_LOGWATCHES=0
LOGWATCH_ID=0

# 5 to include traces; 
# 10 for debug; 20 info, 25 success; 
# 30 warning, 40 error, 50 critical;
LOG_LEVEL = 10

logger.remove()
logger.add(
    sink=sys.stderr,
    format=LOG_FORMAT, 
    level=LOG_LEVEL
)


CONTEXT='fork'
# default num proc is?
mp_cpu_count=mp.cpu_count()
if mp_cpu_count==1: DEFAULT_NUM_PROC=1
elif mp_cpu_count==2: DEFAULT_NUM_PROC=2
elif mp_cpu_count==3: DEFAULT_NUM_PROC=2
else: DEFAULT_NUM_PROC = mp_cpu_count - 2


from .yapmap import *
from .logmap import *