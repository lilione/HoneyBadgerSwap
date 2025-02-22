import sys

from web3 import Web3
from web3.middleware import geth_poa_middleware
from ratel.src.python.deploy import ws_provider, app_addr, token_addrs
from ratel.src.python.utils import fp, decimal, getAccount, sign_and_send, parse_contract


def approve(tokenContract, receiver, amt):
    tx = tokenContract.functions.approve(receiver, int(amt * fp)).buildTransaction({
        'nonce': web3.eth.get_transaction_count(web3.eth.defaultAccount)
    })
    sign_and_send(tx, web3, account)


def deposit(appContract, tokenAddr, depositAmt):
    if tokenAddr == token_addrs[0]:
        tx = appContract.functions.publicDeposit(tokenAddr, int(depositAmt * fp)).buildTransaction({
            'value': int(depositAmt * decimal),
            'nonce': web3.eth.get_transaction_count(web3.eth.defaultAccount)
        })
        sign_and_send(tx, web3, account)

    else:
        abi, bytecode = parse_contract('Token')
        tokenContract = web3.eth.contract(address=tokenAddr, abi=abi)
        approve(tokenContract, appContract.address, int(depositAmt * decimal))

        tx = appContract.functions.publicDeposit(tokenAddr, int(depositAmt * fp)).buildTransaction({
            'nonce': web3.eth.get_transaction_count(web3.eth.defaultAccount)
        })
        sign_and_send(tx, web3, account)

    tx = appContract.functions.secretDeposit(tokenAddr, int(depositAmt * fp)).buildTransaction({
        'nonce': web3.eth.get_transaction_count(web3.eth.defaultAccount)
    })
    sign_and_send(tx, web3, account)


if __name__=='__main__':
    client_id = int(sys.argv[1])
    token_id = int(sys.argv[2])
    depositAmt = int(sys.argv[3])
    print('token_id', token_id)

    web3 = Web3(ws_provider)
    web3.middleware_onion.inject(geth_poa_middleware, layer=0)

    abi, bytecode = parse_contract('hbswap_zkp')
    appContract = web3.eth.contract(address=app_addr, abi=abi)

    account = getAccount(web3, f'/opt/poa/keystore/client_{client_id}/')
    web3.eth.defaultAccount = account.address
    deposit(appContract, token_addrs[token_id], depositAmt)
    print('**** deposit finished')