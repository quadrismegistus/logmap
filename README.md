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

    testing... @ 2023-12-12 11:41:41,203
    ⎿ 0 seconds @ 2023-12-12 11:41:41,204


### Getting duration


```python
# get duration
with logmap('testing...') as lw:
    naptime = lw.nap()

assert naptime == lw.duration
```

    testing... @ 2023-12-12 11:41:45,288
    ￨ napping for 0.7 seconds @ 2023-12-12 11:41:45,289
    ⎿ 0.7 seconds @ 2023-12-12 11:41:45,990


### Nested logging


```python
with logmap('testing nested logging') as lw:
    with logmap('opening nest level 2') as lw2:
        with logmap('opening nest level 3') as lw3:
            lw3.nap()
```

    testing nested logging @ 2023-12-12 11:43:35,657
    ￨ opening nest level 2 @ 2023-12-12 11:43:35,658
    ￨ ￨ opening nest level 3 @ 2023-12-12 11:43:35,659
    ￨ ￨ ￨ napping for 0.3 seconds @ 2023-12-12 11:43:35,660
    ￨ ￨ ⎿ 0.3 seconds @ 2023-12-12 11:43:35,963
    ￨ ⎿ 0.3 seconds @ 2023-12-12 11:43:35,964
    ⎿ 0.3 seconds @ 2023-12-12 11:43:35,964


### Mapping


```python
import random,time

# get objs to map
objs = list(range(10))

# define function to map
def function_to_map(naptime):
    naptime = random.random() * naptime / 2
    time.sleep(naptime)
    return naptime

# open the logmap
with logmap('testing function mapping') as lw:

    # get results in list...
    results = lw.map(function_to_map, objs, num_proc=2)

    # ...or get a generator for results as they arrive (in order)
    results_iter = lw.imap(function_to_map, objs, num_proc=2)
    for res in results_iter:
        lw.log(f'got result: {res:.02}')
```

    testing function mapping @ 2023-12-12 12:45:23,074
    ￨ mapping function_to_map to 10 objects [2x]: 100%|██████████| 10/10 [00:09<00:00,  1.06it/s]
    ￨ got result: 4.0 [2x]: 100%|██████████| 10/10 [00:07<00:00,  1.37it/s]             
    ⎿ 16.8 seconds @ 2023-12-12 12:45:39,858

