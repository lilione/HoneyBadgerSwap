import aiohttp_cors
import aio_eth
import asyncio
import eth_abi
import json
import re
import time

from aiohttp import web, ClientSession
from hexbytes import HexBytes
from ratel.src.python.Client import send_requests, batch_interpolate
from ratel.src.python.deploy import http_uri
from ratel.src.python.utils import threshold_available_preprocessed_elements, prime, \
    http_host, http_port, mpc_port, location_db, openDB, getAccount, \
    confirmation, preprocessed_element_gen_batch_size, list_to_str, execute_cmd, \
    sign_and_send, encode_key, key_state_mask, read_db, bytes_to_dict, dict_to_bytes, write_db, \
    sleep_time, PreprocessedElement, random_int_prog, random_bit_prog, random_triple_prog, \
    key_preprocessed_element_data, key_preprocessed_element_version, prep_dir, location_prep_file, BUFFER_SIZE, \
    bit_size, chunk_size


class Server:
    def __init__(self, serverID, web3, contract, init_players, init_threshold, concurrency, recover, test_recover=False):
        self.serverID = serverID

        self.db = openDB(location_db(serverID))

        self.host = http_host
        self.http_port = http_port + serverID

        self.client_session_pool = ClientSession() ### timeout 300sec

        self.contract = contract
        self.web3 = web3
        self.account = getAccount(web3, f'/opt/poa/keystore/server_{serverID}/')

        self.confirmation = confirmation

        self.players = init_players
        self.threshold = init_threshold

        self.concurrency = concurrency

        self.portLock = {}
        for i in range(-1, concurrency):
            self.portLock[mpc_port + i * 100] = asyncio.Lock()

        self.dbLock = {}
        self.dbLockCnt = {}

        self.loop = asyncio.get_event_loop()

        self.zkrpShares = {}
        self.local_input_mask_cnt = 0
        self.local_zkrp_blinding_share_cnt = 0
        self.local_zkrp_blinding_com_cnt = 0
        self.used_zkrp_blinding_share = 0
        self.used_zkrp_blinding_com = 0
        self.zkrp_blinding_commitment = []

        self.preprocessed_element_cache = {}
        self.preprocessed_element_version = {}
        for element_type in PreprocessedElement:
            self.preprocessed_element_cache[element_type] = []
            self.preprocessed_element_version[element_type] = 0

        self.recover = recover

        self.test_recover = test_recover

    async def init(self, monitor):
        tasks = [
            self.prepare(),
            monitor,
            self.http_server(),
            self.preprocessing()
        ]
        await asyncio.gather(*tasks)


    async def http_server(self):
        async def handler_inputmask(request):
            print(f"s{self.serverID} request: {request}")
            mask_idxes = re.split(",", request.match_info.get("mask_idxes"))
            res = ""
            for mask_idx in mask_idxes:
                res += f"{',' if len(res) > 0 else ''}{int.from_bytes(bytes(self.db.Get(key_preprocessed_element_data(PreprocessedElement.INT, mask_idx))), 'big')}"
            data = {
                "inputmask_shares": res,
            }
            # print(f"s{self.serverID} response: {res}")
            return web.json_response(data)

        async def handler_recover_db(request):
            # print(f"s{self.serverID} request: {request}")
            server_addr = request.match_info.get("server_addr")
            seq_recover_state = int(request.match_info.get("seq_recover_state"))
            seq_num_list = re.split(',', request.match_info.get("list"))
            print(f'num tasks {len(seq_num_list)}')

            keys = await self.collect_keys(seq_num_list)
            masked_states = await self.mask_states(server_addr, seq_recover_state, keys)
            print(f'num states {len(keys)}')

            res = list_to_str(masked_states)

            data = {
                "values": res,
            }
            # print(f"s{self.serverID} response: {res}")
            # print(len(res.encode('utf-8')))
            return web.json_response(data)

        async def handler_open_commitment(request):
            # print(f"s{self.serverID} request: s{request} request from {request.remote}")
            mask_idxes = re.split(',', request.match_info.get("mask_idxes"))

            for mask_idx in mask_idxes:
                while mask_idx not in self.zkrpShares.keys():
                    await asyncio.sleep(0.01)

            res = ""
            for mask_idx in mask_idxes:
                res += f"{';' if len(res) > 0 else ''}{json.dumps(self.zkrpShares[mask_idx])}"

            data = {
                "zkrp_shares": res,
            }
            # print(f"s{self.serverID} response: {res}")
            return web.json_response(data)

        async def handler_get_secret_values(request):
            print(f"s{self.serverID} request: {request}")
            keys = re.split(",", request.match_info.get("keys"))

            res = ""
            for key in keys:
                t1 = encode_key(key)
                int.from_bytes(bytes(self.db.Get(t1)), 'big')
                res += f"{',' if len(res) > 0 else ''}{int.from_bytes(bytes(self.db.Get(encode_key(key))), 'big')}"
            data = {
                "secret_shares": res,
            }
            print(f"s{self.serverID} response: {res}")
            return web.json_response(data)


        app = web.Application()

        cors = aiohttp_cors.setup(
            app,
            defaults={
                "*": aiohttp_cors.ResourceOptions(
                    allow_credentials=True,
                    expose_headers="*",
                    allow_headers="*",
                )
            },
        )

        resource = cors.add(app.router.add_resource("/inputmasks/{mask_idxes}"))
        cors.add(resource.add_route("GET", handler_inputmask))
        resource = cors.add(app.router.add_resource("/recoverdb/{server_addr}-{seq_recover_state}-{list}"))
        cors.add(resource.add_route("GET", handler_recover_db))
        resource = cors.add(app.router.add_resource("/query_secret_values/{keys}"))
        cors.add(resource.add_route("GET", handler_get_secret_values))
        resource = cors.add(app.router.add_resource("/zkrp_share_idxes/{mask_idxes}"))
        cors.add(resource.add_route("GET", handler_open_commitment))

        print(f"Starting http server on {self.host}:{self.http_port}...")
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host=self.host, port=self.http_port)
        await site.start()
        await asyncio.sleep(100 * 3600)


    async def request_state_mask(self, num):
        tx = self.contract.functions.genStateMask(num).buildTransaction(
            {'from': self.account.address, 'gas': 1000000,
             'nonce': self.web3.eth.get_transaction_count(self.account.address)})
        sign_and_send(tx, self.web3, self.account)


    async def gen_preprocessed_elements(self, element_type, batch_size=preprocessed_element_gen_batch_size):
        print(f'Generating {batch_size} {str(element_type)}... s-{self.serverID}')

        if element_type == PreprocessedElement.INT:
            cmd = f'{random_int_prog} -i {self.serverID} -N {self.players} -T {self.threshold} ' \
                  f'--nshares {batch_size} --prep-dir ' \
                  f'{prep_dir(element_type)} -P {prime}'
            await execute_cmd(cmd)

            file = location_prep_file(element_type, self.serverID, self.players)
            shares = []
            with open(file, "r") as f:
                for line in f.readlines():
                    share = int(line) % prime
                    shares.append(share)
            return shares

        elif element_type == PreprocessedElement.BIT:
            cmd = f'{random_bit_prog} -i {self.serverID} -N {self.players} -T {self.threshold} ' \
                  f'-s {batch_size} --prep-dir {prep_dir(element_type)} -P {prime}'
            await execute_cmd(cmd)

            file = location_prep_file(element_type, self.serverID, self.players)
            chunks = []
            with open(file, "rb") as f:
                data = f.read()
                for i in range(batch_size // BUFFER_SIZE):
                    chunks.append(data[bit_size * chunk_size[element_type] * i:
                                       bit_size * chunk_size[element_type] * (i + 1)])
            return chunks

        elif element_type == PreprocessedElement.TRIPLE:
            cmd = f'{random_triple_prog} -i {self.serverID} -N {self.players} -T {self.threshold} ' \
                  f'-s {batch_size} --prep-dir {prep_dir(element_type)} -P {prime}'
            await execute_cmd(cmd)

            file = location_prep_file(element_type, self.serverID, self.players)
            chunks = []
            with open(file, "rb") as f:
                data = f.read()
                for i in range(batch_size // BUFFER_SIZE):
                    chunks.append(data[3 * bit_size * chunk_size[element_type] * i:
                                       3 * bit_size * chunk_size[element_type] * (i + 1)])
            return chunks


    async def get_zkrp_shares(self, players, inputmask_idxes):
        request = f"zkrp_share_idxes/{inputmask_idxes}"
        results_list = await send_requests(players, request, self.client_session_pool)
        for i in range(len(results_list)):
            results_list[i] = re.split(";", results_list[i]["zkrp_shares"])

        results = []
        num = len(results_list[0])
        for j in range(num):
            tmp_res = []
            for i in range(len(results_list)):
                tmp_res.append(json.loads(results_list[i][j]))
            results.append(tmp_res)

        return results


    async def preprocessing(self):
        # TODO: remove the following & add generating agreement proof
        if self.serverID != 0:
            return

        while True:
            for element_type in PreprocessedElement:
                num_used = self.contract.functions.numUsedPreprocessedElement(element_type).call()
                num_total = self.contract.functions.numTotalPreprocessedElement(element_type).call()
                print(f'Preprocessing: {num_total - num_used} {str(element_type)} left...')
                if num_total - num_used < threshold_available_preprocessed_elements:
                    print(f'Initialize {str(element_type)} generation process....')
                    tx = self.contract.functions.initGenPreprocessedElement(element_type, True).buildTransaction(
                        {'from': self.account.address, 'gas': 1000000,
                         'nonce': self.web3.eth.get_transaction_count(self.account.address)})
                    sign_and_send(tx, self.web3, self.account)
                # num_used_input_mask = self.contract.functions.numUsedInputMask().call()
                # num_total_input_mask = self.contract.functions.numTotalInputMask().call()
                # if num_total_input_mask - num_used_input_mask < threshold_available_input_masks:
                #     print(f'Initialize input mask generation process....')
                #     tx = self.contract.functions.initGenInputMask(True).buildTransaction(
                #         {'from': self.account.address, 'gas': 1000000,
                #          'nonce': self.web3.eth.get_transaction_count(self.account.address)})
                #     sign_and_send(tx, self.web3, self.account)

            await asyncio.sleep(10)

    async def prepare(self, repetition=1):
        # TODO: consider the ordering of crash recovery related functions
        is_server = self.contract.functions.isServer(self.account.address).call()
        print(f's-{self.serverID} {is_server}')
        if not is_server:
            print('crash recovering...')
            # TODO: acquire approval from other servers
            tx = self.contract.functions.addServer(self.account.address).buildTransaction({
                'from': self.account.address,
                'gas': 1000000,
                'nonce': self.web3.eth.get_transaction_count(self.account.address)
            })
            sign_and_send(tx, self.web3, self.account)

            if not self.test_recover:
                await self.check_input_mask()

            seq_num_list = self.check_missing_tasks() * repetition
            print(f'seq_num_list {seq_num_list}')
            if len(seq_num_list) == 0:
                return

            ### TODO: delete this for crash recovery benchmark
            await self.gen_state_mask(repetition)

            await self.recover_history(seq_num_list, repetition)

    async def check_input_mask(self):
        version_input_mask = self.contract.functions.versionInputMask().call()
        num_total_input_mask = self.contract.functions.numTotalInputMask().call()
        print(f'version_input_mask {version_input_mask}')
        print(f'num_total_input_mask {num_total_input_mask}')

        out_of_date = False
        try:
            local_version = int.from_bytes(bytes(self.db.Get(key_preprocessed_element_version(num_total_input_mask - 1))), 'big')
            if local_version < version_input_mask:
                out_of_date = True
        except KeyError:
            out_of_date = True

        if out_of_date:
            tx = self.contract.functions.initGenInputMask(True).buildTransaction({
                'from': self.account.address,
                'gas': 1000000,
                'nonce': self.web3.eth.get_transaction_count(self.account.address)
            })
            sign_and_send(tx, self.web3, self.account)

            while True:
                try:
                    print(f'idx {num_total_input_mask - 1}')
                    local_version = int.from_bytes(bytes(self.db.Get(key_preprocessed_element_version(num_total_input_mask - 1))), 'big')
                    print(f'local_version {local_version}')
                    if local_version > version_input_mask:
                        break
                except:
                    pass
                await asyncio.sleep(sleep_time)

    async def recover_history(self, seq_num_list, repetition):
        print(f'start benchmarking recover_history...')
        ### benchmark
        times = []
        times.append(time.perf_counter())

        keys = await self.collect_keys(seq_num_list)
        # print(f'keys {keys}')

        ### benchmark
        times.append(time.perf_counter())

        num_states_to_recover = len(keys)
        await self.gen_state_mask(num_states_to_recover)

        ### benchmark
        times.append(time.perf_counter())
        seq_recover_state = await self.consume_state_mask(num_states_to_recover)

        ### benchmark
        times.append(time.perf_counter())

        request = f'recoverdb/{self.account.address}-{seq_recover_state}-{list_to_str(seq_num_list)}'
        # print(request)
        # print(len(request.encode('utf-8')))

        masked_states = await send_requests(self.players, request, self.client_session_pool, self.serverID)
        # print(masked_states)

        ### benchmark
        times.append(time.perf_counter())

        batch_points = []
        for i in range(len(masked_states)):
            if len(masked_states[i]):
                batch_points.append((i + 1, re.split(",", masked_states[i]["values"])))
        masked_states = batch_interpolate(self.serverID + 1, batch_points, self.threshold)
        state_shares = self.unmask_states(masked_states, seq_recover_state)

        ### benchmark
        times.append(time.perf_counter())

        self.restore_db(seq_num_list, keys, state_shares)

        ### benchmark
        times.append(time.perf_counter())

        ### benchmark
        with open(f'ratel/benchmark/data/recover_states_{repetition}.csv', 'a') as f:
            for op, t in enumerate(times):
                f.write(f'op\t{op + 1}\t'
                        f'cur_time\t{t}\n')

        # TODO: recover states of on-going MPC tasks

    def check_missing_tasks(self):
        key = 'execHistory'
        exec_history = read_db(self, key)
        exec_history = bytes_to_dict(exec_history)

        seq_list = []

        finalized_task_cnt = self.contract.functions.finalizedTaskCnt().call()
        print(f'finalized_task_cnt {finalized_task_cnt}')
        for finalized_seq in range(1, 1 + finalized_task_cnt):
            if finalized_seq not in exec_history or not exec_history[finalized_seq]:
                init_seq = self.contract.functions.finalized(finalized_seq).call()
                print(f'missing task with initSeq {init_seq} finalizedSeq {finalized_seq}')
                seq_list.append(init_seq)

        return seq_list

    async def collect_keys(self, seq_num_list):
        if not self.test_recover:
            seq_num_list = list(set(seq_num_list))

        # keys = []
        # for seq_num in seq_num_list:
        #     keys.extend(self.recover(self.contract, int(seq_num), 'writeSet'))

        times = []
        times.append(time.perf_counter())

        async with aio_eth.EthAioAPI(http_uri, max_tasks=2 * len(seq_num_list)) as api:
            for seq_num in seq_num_list:
                data = self.contract.encodeABI(fn_name='opEvent', args=[int(seq_num)])
                api.push_task({
                    "method": "eth_call",
                    "params": [
                        {
                            "to": self.contract.address,
                            "data": data,
                        },
                        "latest"
                    ]
                })

                data = self.contract.encodeABI(fn_name='opContent', args=[int(seq_num)])
                api.push_task({
                    "method": "eth_call",
                    "params": [
                        {
                            "to": self.contract.address,
                            "data": data,
                        },
                        "latest"
                    ]
                })

            results = await api.exec_tasks_batch()
            # results = await api.exec_tasks_async()

        # times.append(time.perf_counter())
        #
        # list_op_event = []
        # lsit_op_content = []
        # for seq_num, res_op_event, res_op_content in zip(seq_num_list, results[0::2], results[1::2]):
        #     op_event = eth_abi.decode_abi(['string'], HexBytes(res_op_event['result']))[0]
        #     op_content = eth_abi.decode_abi(['bytes'], HexBytes(res_op_content['result']))[0]
        #     list_op_event.append(op_event)
        #     lsit_op_content.append(op_content)
        #
        # times.append(time.perf_counter())
        #
        # keys = []
        # for seq_num, op_event, op_content in zip(seq_num_list, list_op_event, lsit_op_content):
        #     keys.extend(self.recover.parse(op_event, op_content, seq_num, 'writeSet'))
        #
        # times.append(time.perf_counter())
        # for i in range(1, len(times)):
        #     print('!', times[i] - times[i - 1])

        keys = []
        for seq_num, res_op_event, res_op_content in zip(seq_num_list, results[0::2], results[1::2]):
            op_event = eth_abi.decode_abi(['string'], HexBytes(res_op_event['result']))[0]
            op_content = eth_abi.decode_abi(['bytes'], HexBytes(res_op_content['result']))[0]
            keys.extend(self.recover.parse(op_event, op_content, seq_num, 'writeSet'))

        if not self.test_recover:
            keys = list(set(keys))

        return keys

    async def gen_state_mask(self, num):
        num_total_state_mask = self.contract.functions.numTotalStateMask(self.account.address).call()
        num_used_state_mask = self.contract.functions.numUsedStateMask(self.account.address).call()
        num_to_gen = max(0, num - num_total_state_mask + num_used_state_mask)

        if num_to_gen > 0:
            print(f'generating {num_to_gen} state masks...')
            tx = self.contract.functions.genStateMask(num_to_gen).buildTransaction({
                'from': self.account.address,
                'gas': 1000000,
                'nonce': self.web3.eth.get_transaction_count(self.account.address)
            })
            receipt = sign_and_send(tx, self.web3, self.account)
            logs = self.contract.events.GenStateMask().processReceipt(receipt)
            init_state_mask_index = logs[0]['args']['initStateMaskIndex']
            num = logs[0]['args']['num']

            while True:
                try:
                    self.db.Get(key_state_mask(self.account.address, init_state_mask_index + num - 1))
                    break
                except KeyError:
                    await asyncio.sleep(sleep_time)

    async def consume_state_mask(self, num):
        tx = self.contract.functions.consumeStateMask(num).buildTransaction({
            'from': self.account.address,
            'gas': 1000000,
            'nonce': self.web3.eth.get_transaction_count(self.account.address)
        })
        receipt = sign_and_send(tx, self.web3, self.account)
        logs = self.contract.events.RecoverState().processReceipt(receipt)
        seq_recover_state = logs[0]['args']['seqRecoverState']

        while True:
            if self.web3.eth.get_block_number() - receipt['blockNumber'] > self.confirmation:
                return seq_recover_state

    async def mask_states(self, server_addr, seq_recover_state, keys):
        # TODO: deal with the case that malicious MPC server reuse the same seq_num
        # TODO: handle the case when the server does not have the share of some state masks

        masked_states = []

        init_index_recover_state = self.contract.functions.initIndexRecoverState(server_addr, seq_recover_state).call()
        num_recover_state = self.contract.functions.numRecoverState(server_addr, seq_recover_state).call()

        if num_recover_state != len(keys):
            print(f'invalid recover state request')
            return masked_states

        for idx, key in zip(range(init_index_recover_state, init_index_recover_state + num_recover_state), keys):
            state = int.from_bytes(bytes(self.db.Get(key.lower().encode())), 'big')
            state_mask_share = int.from_bytes(bytes(self.db.Get(key_state_mask(server_addr, idx))), 'big')
            masked_state_share = (state + state_mask_share) % prime
            masked_states.append(masked_state_share)

        return masked_states

    def unmask_states(self, masked_states, seq_recover_state):
        state_shares = []

        init_index_recover_state = self.contract.functions.initIndexRecoverState(self.account.address, seq_recover_state).call()
        num_recover_state = self.contract.functions.numRecoverState(self.account.address, seq_recover_state).call()

        for idx, masked_state in zip(range(init_index_recover_state, init_index_recover_state + num_recover_state), masked_states):
            state_mask_share = int.from_bytes(bytes(self.db.Get(key_state_mask(self.account.address, idx))), 'big')
            state_share = (masked_state - state_mask_share) % prime
            state_shares.append(state_share)

        return state_shares

    def restore_db(self, seq_num_list, keys, values):
        assert len(keys) == len(values)

        for key, value in zip(keys, values):
            self.db.Put(key.encode(), value.to_bytes((value.bit_length() + 7) // 8, 'big'))

        key = 'execHistory'
        exec_history = read_db(self, key)
        exec_history = bytes_to_dict(exec_history)

        for seq in seq_num_list:
            exec_history[seq] = True

        exec_history = dict_to_bytes(exec_history)
        write_db(self, key, exec_history)
