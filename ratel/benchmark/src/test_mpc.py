import asyncio
import matplotlib.pyplot as plt
import shutil
import sys
import time

from ratel.src.python.utils import mpc_port, repeat_experiment


def set_up_share_files(players, concurrency):
    for i in range(concurrency):
        port = mpc_port + i * 100
        for server_id in range(players):
            shutil.copyfile(f'ratel/benchmark/data/sharefiles/Transactions-P{server_id}-{mpc_port}.data', f'Persistence/Transactions-P{server_id}-{port}.data')


async def test(func, server_id, port, players, threshold, mpcProg):
    start_time = time.perf_counter()

    await eval(func)(server_id, port, players, threshold, mpcProg)

    end_time = time.perf_counter()
    duration = end_time - start_time

    return duration


async def run_test(func, players, threshold, concurrency, mpcProg):
    tasks = []
    for i in range(concurrency):
        port = mpc_port + i * 100
        for server_id in range(players):
            tasks.append(test(func, server_id, port, players, threshold, mpcProg))
    results = await asyncio.gather(*tasks)
    print(f'!!! {func} {results}')
    return sum(results) / (players * concurrency)


async def rep(func, players, threshold, concurrency, mpcProg):
    sum = 0
    for i in range(repeat_experiment):
        sum += await run_test(func, players, threshold, concurrency, mpcProg)
    avg = sum / repeat_experiment
    print(f'!!!! {func} {avg}')
    return avg


async def main(players, threshold, max_concurrency):
    x, y_offline, y_online, y_online_ONLY = [], [], [], []
    for concurrency in range(1, 1 + max_concurrency):
        x.append(concurrency)
        y_offline.append(await rep('run_offline', players, threshold, concurrency, mpcProg))
        y_online.append(await rep('run_online', players, threshold, concurrency, mpcProg))
        y_online_ONLY.append(await rep('run_online_ONLY', players, threshold, concurrency, mpcProg))

    with open('ratel/benchmark/data/mp-spdz.txt', 'w') as f:
        f.write(str(x) + '\n')
        f.write(str(y_offline) + '\n')
        f.write(str(y_online) + '\n')
        f.write(str(y_online_ONLY) + '\n')

    plt.figure(figsize=(13, 4))
    plt.scatter(x, y_offline)
    plt.scatter(x, y_online)
    plt.scatter(x, y_online_ONLY)
    plt.savefig(f'ratel/benchmark/data/mp-spdz.pdf')


if __name__ == '__main__':
    players = int(sys.argv[1])
    threshold = int(sys.argv[2])
    max_concurrency = int(sys.argv[3])
    mpcProg = sys.argv[4]

    set_up_share_files(players, max_concurrency)
    asyncio.run(main(players, threshold, max_concurrency))



