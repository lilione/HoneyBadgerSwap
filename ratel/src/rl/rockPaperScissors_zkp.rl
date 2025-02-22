pragma solidity ^0.5.0;

import "@openzeppelin/contracts/math/SafeMath.sol";
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/SafeERC20.sol";

contract rockPaperScissors {
    using SafeMath for uint;
    using SafeERC20 for IERC20;

    uint public gameCnt;
    mapping (uint => address) public gamePlayer1;
    mapping (uint => address) public gamePlayer2;

    mapping (uint => uint) public status; // active-1, ready-2, completed-3

    mapping (uint => string) public winners;


    constructor() public {}


    function createGame($uint value1) public {
        address player1 = msg.sender;
        uint gameId = ++gameCnt;
        gamePlayer1[gameId] = player1;

        mpc(uint gameId, address player1, $uint value1) {
            assert(zkrp(value1 >= 1; value1 <= 3))

            writeDB(f'game_value1_{gameId}', value1, int)

            curStatus = 1
            set(status, uint curStatus, uint gameId)
        }
    }


    function joinGame(uint gameId, $uint value2) public {
        require(status[gameId] == 1);
        address player2 = msg.sender;
        gamePlayer2[gameId] = player2;

        mpc(uint gameId, address player2, $uint value2) {
            assert(zkrp(value2 >= 1; value2 <= 3))

            writeDB(f'game_value2_{gameId}', value2, int)

            curStatus = 2
            set(status, uint curStatus, uint gameId)
        }
    }


    function startRecon(uint gameId) public { // 1 < 2; 2 < 3; 3 < 1;
        require(status[gameId] == 2);
        status[gameId]++;

        mpc(uint gameId) {
            value1 = readDB(f'game_value1_{gameId}', int)
            value2 = readDB(f'game_value2_{gameId}', int)

            mpcInput(sint value1, sint value2)
            print_ln('**** value1 %s', value1.reveal())
            print_ln('**** value2 %s', value2.reveal())

            result = (value1 - value2).reveal()

            print_ln('**** result %s', result)
            mpcOutput(cint result)

            if result > 2:
                result -= prime
            print('****', result)
            if result == 0:
                print('**** tie')
                winner = 'tie'
            elif result == 1 or result == -2:
                print('**** winner-player1')
                winner = 'player1'
            else:
                print('**** winner-player2')
                winner = 'player2'

            set(winners, string memory winner, uint gameId)
        }
    }
}
