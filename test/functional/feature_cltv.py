#!/usr/bin/env python3
# Copyright (c) 2015-2019 The Bitcoin Core developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
"""Test BIP65 (CHECKLOCKTIMEVERIFY).

Test that the CHECKLOCKTIMEVERIFY soft-fork activates at (regtest) block height
1351.
"""

from decimal import Decimal

from test_framework.blocktools import (
    create_block,
    create_coinbase,
    create_transaction,
    prepare_block,
    SUBSIDY,
)
from test_framework.messages import (
    CTransaction,
    FromHex,
    ToHex,
    msg_block,
    msg_tx,
)
from test_framework.p2p import P2PInterface
from test_framework.script import (
    OP_1NEGATE,
    OP_CHECKLOCKTIMEVERIFY,
    OP_DROP,
    OP_TRUE,
    CScript,
    CScriptNum,
)
from test_framework.test_framework import BitcoinTestFramework
from test_framework.txtools import pad_tx
from test_framework.util import assert_equal

CLTV_HEIGHT = 1351


def cltv_lock_to_height(node, tx, to_address, amount, height=-1):
    '''Modify the scriptPubKey to add an OP_CHECKLOCKTIMEVERIFY, and make
    a transaction that spends it.

    This transforms the output script to anyone can spend (OP_TRUE) if the
    lock time condition is valid.

    Default height is -1 which leads CLTV to fail

    TODO: test more ways that transactions using CLTV could be invalid (eg
    locktime requirements fail, sequence time requirements fail, etc).
    '''
    height_op = OP_1NEGATE
    if(height > 0):
        tx.vin[0].nSequence = 0
        tx.nLockTime = height
        height_op = CScriptNum(height)

    tx.vout[0].scriptPubKey = CScript(
        [height_op, OP_CHECKLOCKTIMEVERIFY, OP_DROP, OP_TRUE])

    pad_tx(tx)
    fundtx_raw = node.signrawtransactionwithwallet(ToHex(tx))['hex']

    fundtx = FromHex(CTransaction(), fundtx_raw)
    fundtx.rehash()

    # make spending tx
    inputs = [{
        "txid": fundtx.txid_hex,
        "vout": 0
    }]
    output = {to_address: amount}

    spendtx_raw = node.createrawtransaction(inputs, output)

    spendtx = FromHex(CTransaction(), spendtx_raw)
    pad_tx(spendtx)

    return fundtx, spendtx


class BIP65Test(BitcoinTestFramework):
    def set_test_params(self):
        self.num_nodes = 1
        self.extra_args = [[
            '-whitelist=noban@127.0.0.1',
            '-par=1',  # Use only one script thread to get the exact reject reason for testing
            '-acceptnonstdtxn=1',  # cltv_invalidate is nonstandard
        ]]
        self.setup_clean_chain = True
        self.rpc_timeout = 120

    def skip_test_if_missing_module(self):
        self.skip_if_no_wallet()

    def run_test(self):
        peer = self.nodes[0].add_p2p_connection(P2PInterface())

        self.log.info("Mining {} blocks".format(CLTV_HEIGHT - 2))
        self.coinbase_txids = [self.nodes[0].getblock(
            b)['tx'][0] for b in self.nodes[0].generate(CLTV_HEIGHT - 2)]
        self.nodeaddress = self.nodes[0].getnewaddress()

        self.log.info(
            "Test that an invalid-according-to-CLTV transaction cannot appear in a block")

        fundtx = create_transaction(self.nodes[0], self.coinbase_txids[0],
                                    self.nodeaddress, amount=SUBSIDY - Decimal('1'), vout=1)
        fundtx, spendtx = cltv_lock_to_height(
            self.nodes[0], fundtx, self.nodeaddress, SUBSIDY - Decimal('2'))

        tip = self.nodes[0].getbestblockhash()
        block_time = self.nodes[0].getblockheader(tip)['mediantime'] + 1
        block = create_block(int(tip, 16), create_coinbase(
            CLTV_HEIGHT - 1), CLTV_HEIGHT,  block_time)
        block.vtx.append(fundtx)
        # include the -1 CLTV in block
        block.vtx.append(spendtx)
        prepare_block(block)

        peer.send_and_ping(msg_block(block))
        # This block is invalid
        assert self.nodes[0].getbestblockhash() != block.hash
        
        # Create valid block to get over the threshold for the version enforcement
        block = create_block(int(tip, 16), create_coinbase(
            CLTV_HEIGHT - 1), CLTV_HEIGHT - 1, block_time)
        prepare_block(block)
        peer.send_and_ping(msg_block(block))

        tip = block.sha256
        block_time += 1
        self.log.info(
            "Test that invalid-according-to-cltv transactions cannot appear in a block")
        block = create_block(tip, create_coinbase(CLTV_HEIGHT), CLTV_HEIGHT, block_time)

        fundtx = create_transaction(self.nodes[0], self.coinbase_txids[1],
                                    self.nodeaddress, amount=SUBSIDY - Decimal('1'), vout=1)
        fundtx, spendtx = cltv_lock_to_height(
            self.nodes[0], fundtx, self.nodeaddress, SUBSIDY - Decimal('2'))

        # The funding tx only has unexecuted bad CLTV, in scriptpubkey; this is
        # valid.
        peer.send_and_ping(msg_tx(fundtx))
        assert fundtx.txid_hex in self.nodes[0].getrawmempool()

        # Mine a block containing the funding transaction
        block.vtx.append(fundtx)
        prepare_block(block)

        peer.send_and_ping(msg_block(block))
        # This block is valid
        assert_equal(self.nodes[0].getbestblockhash(), block.hash)

        # We show that this tx is invalid due to CLTV by getting it
        # rejected from the mempool for exactly that reason.
        assert_equal(
            [{'txid': spendtx.txid_hex, 'allowed': False,
              'reject-reason': 'mandatory-script-verify-flag-failed (Negative locktime)'}],
            self.nodes[0].testmempoolaccept(
                rawtxs=[spendtx.serialize().hex()], maxfeerate=0)
        )

        rejectedtx_signed = self.nodes[0].signrawtransactionwithwallet(
            ToHex(spendtx))

        # Couldn't complete signature due to CLTV
        assert rejectedtx_signed['errors'][0]['error'] == 'Negative locktime'

        tip = block.hash
        block_time += 1
        block = create_block(
            block.sha256, create_coinbase(CLTV_HEIGHT + 1), CLTV_HEIGHT + 1, block_time)
        block.vtx.append(spendtx)
        prepare_block(block)

        with self.nodes[0].assert_debug_log(expected_msgs=['ConnectBlock {} failed, blk-bad-inputs'.format(block.hash)]):
            peer.send_and_ping(msg_block(block))
            assert_equal(self.nodes[0].getbestblockhash(), tip)
            peer.sync_with_ping()

        self.log.info(
            "Test that a version 4 block with a valid-according-to-CLTV transaction is accepted")
        fundtx = create_transaction(self.nodes[0], self.coinbase_txids[2],
                                    self.nodeaddress, amount=SUBSIDY - Decimal('1'), vout=1)
        fundtx, spendtx = cltv_lock_to_height(
            self.nodes[0], fundtx, self.nodeaddress, SUBSIDY - Decimal('2'), CLTV_HEIGHT)

        # make sure sequence is nonfinal and locktime is good
        spendtx.vin[0].nSequence = 0xfffffffe
        spendtx.nLockTime = CLTV_HEIGHT

        # both transactions are fully valid
        self.nodes[0].sendrawtransaction(ToHex(fundtx))
        self.nodes[0].sendrawtransaction(ToHex(spendtx))

        # Modify the transactions in the block to be valid against CLTV
        block.vtx.pop(1)
        block.vtx.append(fundtx)
        block.vtx.append(spendtx)
        prepare_block(block)

        peer.send_and_ping(msg_block(block))
        # This block is now valid
        assert_equal(self.nodes[0].getbestblockhash(), block.hash)


if __name__ == '__main__':
    BIP65Test().main()
