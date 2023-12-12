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
with logmap('testing...') as lw:
    naptime = lw.nap()
```

    testing... @ 2023-12-12 12:58:19,874
    ￨ napping for 0.4 seconds @ 2023-12-12 12:58:19,875
    ⎿ 0.4 seconds @ 2023-12-12 12:58:20,280



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
with logmap('testing function mapping') as lw:

    # get results as a list
    results = lw.map(function_to_map, objs, num_proc=2)

# show results
results
```

    testing function mapping @ 2023-12-12 12:58:20,608
    ￨ mapping function_to_map to 5 objects [2x]: 100%|██████████| 5/5 [00:00<00:00,  5.08it/s]
    ⎿ 1 second @ 2023-12-12 12:58:21,608





    [0.0,
     0.45455356308145395,
     0.032980832312656805,
     0.5999049813004816,
     0.5094736472140351]




```python
# Or get a generator for results as they arrive (in order)
with logmap('testing function mapping') as lw:
    results_iter = lw.imap(function_to_map, objs, num_proc=2)
    for res in results_iter:
        lw.log(f'got result: {res:.02}')
```

    testing function mapping @ 2023-12-12 12:58:21,620
    ￨ got result: 1.1 [2x]: 100%|██████████| 5/5 [00:01<00:00,  2.91it/s]             
    ⎿ 1.7 seconds @ 2023-12-12 12:58:23,345

