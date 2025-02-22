import sys

from ratel.src.python.deploy import ws_provider, app_addr, token_addrs
from ratel.src.python.utils import fp, getAccount, parse_contract, sign_and_send
from web3 import Web3
from web3.middleware import geth_poa_middleware

def initPool(appContract, tokenA, tokenB, amtA, amtB):
    tx = appContract.functions.initPool(tokenA, tokenB, int(amtA * fp), int(amtB * fp)).buildTransaction({
        'nonce': web3.eth.get_transaction_count(web3.eth.defaultAccount)
    })
    sign_and_send(tx, web3, account)

if __name__=='__main__':
    client_id = int(sys.argv[1])
    tokenA = token_addrs[int(sys.argv[2])]
    tokenB = token_addrs[int(sys.argv[3])]
    amtA = int(sys.argv[4])
    amtB = int(sys.argv[5])

    web3 = Web3(ws_provider)
    web3.middleware_onion.inject(geth_poa_middleware, layer=0)

    abi, bytecode = parse_contract('hbswap')
    appContract = web3.eth.contract(address=app_addr, abi=abi)

    account = getAccount(web3, f'/opt/poa/keystore/client_{client_id}/')
    web3.eth.defaultAccount = account.address

    initPool(appContract, tokenA, tokenB, amtA, amtB)