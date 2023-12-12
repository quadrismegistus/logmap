# logmap

A hierarchical, context-manager logger utility with multiprocess mapping capabilities

## Install

```
!pip install git+https://github.com/quadrismegistus/logmap
```

## Usage


```python
from logmap import logmap
```

### Basic usage


```python
with logmap('testing...'):
    # ... do something ...
    pass
```

    testing... @ 2023-12-12 12:58:19,866
    ⎿ 0 seconds @ 2023-12-12 12:58:19,867


### Getting duration


```python
# get duration
with logmap('testing...') as lm:
    naptime = lm.nap()
```

    testing... @ 2023-12-12 12:58:19,874
    ￨ napping for 0.4 seconds @ 2023-12-12 12:58:19,875
    ⎿ 0.4 seconds @ 2023-12-12 12:58:20,280



```python
assert naptime == lm.duration
```

### Nested logging


```python
with logmap('testing nested logging') as lm:
    with logmap('opening nest level 2') as lm2:
        with logmap('opening nest level 3') as lm3:
            lm3.nap()
```

    testing nested logging @ 2023-12-12 12:58:20,292
    ￨ opening nest level 2 @ 2023-12-12 12:58:20,293
    ￨ ￨ opening nest level 3 @ 2023-12-12 12:58:20,294
    ￨ ￨ ￨ napping for 0.3 seconds @ 2023-12-12 12:58:20,294
    ￨ ￨ ⎿ 0.3 seconds @ 2023-12-12 12:58:20,599
    ￨ ⎿ 0.3 seconds @ 2023-12-12 12:58:20,600
    ⎿ 0.3 seconds @ 2023-12-12 12:58:20,601


### Mapping


```python
import random,time

# get objs to map
objs = list(range(5))

# define function to map
def function_to_map(naptime):
    naptime = random.random() * naptime / 2
    time.sleep(naptime)
    return naptime

# open the logmap
with logmap('testing function mapping') as lm:
    # get results as a list
    results = lm.map(function_to_map, objs, num_proc=2)
```

    testing function mapping @ 2023-12-12 13:00:31,037
    ￨ mapping function_to_map to 5 objects [2x]: 100%|██████████| 5/5 [00:02<00:00,  2.09it/s]
    ⎿ 2.4 seconds @ 2023-12-12 13:00:33,433


Or get a generator for results as they arrive (in order):


```python
with logmap('testing function mapping') as lm:
    # this is a generator
    results_iter = lm.imap(function_to_map, objs, num_proc=2)
    # loop as results arrive
    for res in results_iter:
        # this will update progress bar
        lm.log(f'got result: {res:.02}') 
```

    testing function mapping @ 2023-12-12 13:01:23,981
    ￨ got result: 1.7 [2x]: 100%|██████████| 5/5 [00:02<00:00,  1.99it/s]             
    ⎿ 2.5 seconds @ 2023-12-12 13:01:26,500

