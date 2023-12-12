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

    testing... @ 2023-12-12 12:55:14,035
    ⎿ 0 seconds @ 2023-12-12 12:55:14,037


### Getting duration


```python
# get duration
with logmap('testing...') as lw:
    naptime = lw.nap()
```

    testing... @ 2023-12-12 12:55:14,044
    ￨ napping for 0.4 seconds @ 2023-12-12 12:55:14,045
    ⎿ 0.4 seconds @ 2023-12-12 12:55:14,448



```python
assert naptime == lw.duration
```

### Nested logging


```python
with logmap('testing nested logging') as lw:
    with logmap('opening nest level 2') as lw2:
        with logmap('opening nest level 3') as lw3:
            lw3.nap()
```

    testing nested logging @ 2023-12-12 12:55:14,478
    ￨ opening nest level 2 @ 2023-12-12 12:55:14,479
    ￨ ￨ opening nest level 3 @ 2023-12-12 12:55:14,480
    ￨ ￨ ￨ napping for 0.6 seconds @ 2023-12-12 12:55:14,480
    ￨ ￨ ⎿ 0.6 seconds @ 2023-12-12 12:55:15,085
    ￨ ⎿ 0.6 seconds @ 2023-12-12 12:55:15,086
    ⎿ 0.6 seconds @ 2023-12-12 12:55:15,087


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
with logmap('testing function mapping') as lw:

    # get results as a list
    results = lw.map(function_to_map, objs, num_proc=2)

# show results
results
```

    testing function mapping @ 2023-12-12 12:55:15,094
    ￨ mapping function_to_map to 5 objects [2x]: 100%|██████████| 5/5 [00:01<00:00,  2.77it/s]
    ⎿ 1.8 seconds @ 2023-12-12 12:55:16,912





    [0.0,
     0.37160430145215606,
     0.8314377929884459,
     0.4700268826859305,
     0.9458661115488189]




```python
# Or get a generator for results as they arrive (in order)
with logmap('testing function mapping') as lw:
    results_iter = lw.imap(function_to_map, objs, num_proc=2)
    for res in results_iter:
        lw.log(f'got result: {res:.02}')
```

    testing function mapping @ 2023-12-12 12:55:16,925
    ￨ got result: 1.1 [2x]: 100%|██████████| 5/5 [00:01<00:00,  3.17it/s]             
    ⎿ 1.6 seconds @ 2023-12-12 12:55:18,508

