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

    testing... @ 2023-12-12 12:56:00,732
    ⎿ 0 seconds @ 2023-12-12 12:56:00,733


### Getting duration


```python
# get duration
with logmap('testing...') as lw:
    naptime = lw.nap()
```

    testing... @ 2023-12-12 12:56:00,739
    ￨ napping for 1.0 seconds @ 2023-12-12 12:56:00,740
    ⎿ 1 second @ 2023-12-12 12:56:01,745



```python
naptime == lw.duration
```




    True



### Nested logging


```python
with logmap('testing nested logging') as lw:
    with logmap('opening nest level 2') as lw2:
        with logmap('opening nest level 3') as lw3:
            lw3.nap()
```

    testing nested logging @ 2023-12-12 12:56:01,761
    ￨ opening nest level 2 @ 2023-12-12 12:56:01,762
    ￨ ￨ opening nest level 3 @ 2023-12-12 12:56:01,763
    ￨ ￨ ￨ napping for 0.5 seconds @ 2023-12-12 12:56:01,764
    ￨ ￨ ⎿ 0.5 seconds @ 2023-12-12 12:56:02,268
    ￨ ⎿ 0.5 seconds @ 2023-12-12 12:56:02,269
    ⎿ 0.5 seconds @ 2023-12-12 12:56:02,270


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

    testing function mapping @ 2023-12-12 12:56:02,279
    ￨ mapping function_to_map to 5 objects [2x]: 100%|██████████| 5/5 [00:01<00:00,  3.20it/s]
    ⎿ 1.6 seconds @ 2023-12-12 12:56:03,855





    [0.0,
     0.38940430406309995,
     0.42620823441628486,
     1.1463515363207601,
     0.8378910357390124]




```python
# Or get a generator for results as they arrive (in order)
with logmap('testing function mapping') as lw:
    results_iter = lw.imap(function_to_map, objs, num_proc=2)
    for res in results_iter:
        lw.log(f'got result: {res:.02}')
```

    testing function mapping @ 2023-12-12 12:56:03,866
    ￨ got result: 0.23 [2x]: 100%|██████████| 5/5 [00:01<00:00,  4.46it/s]            
    ⎿ 1.1 seconds @ 2023-12-12 12:56:04,993

