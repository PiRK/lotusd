#!/usr/bin/env python3
# Copyright (c) 2010 ArtForz -- public domain half-a-node
# Copyright (c) 2012 Jeff Garzik
# Copyright (c) 2010-2019 The Bitcoin Core developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
"""Bitcoin test framework primitive and message structures

CBlock, CTransaction, CBlockHeader, CTxIn, CTxOut, etc....:
    data structures that should map to corresponding structures in
    bitcoin/primitives

msg_block, msg_tx, msg_headers, etc.:
    data structures that represent network messages

ser_*, deser_*: functions that handle serialization/deserialization.

Classes use __slots__ to ensure extraneous attributes aren't accidentally added
by tests, compromising their intended effect.
"""
import copy
import hashlib
import random
import socket
import struct
import time
import unittest
from base64 import b64decode, b64encode
from codecs import encode
from enum import IntEnum
from io import BytesIO
from typing import List

from test_framework.siphash import siphash256
from test_framework.util import assert_equal, hex_str_to_bytes

MIN_VERSION_SUPPORTED = 60001
# past bip-31 for ping/pong
MY_VERSION = 70014
MY_SUBVERSION = b"/python-p2p-tester:0.0.3/"
# from version 70001 onwards, fRelay should be appended to version
# messages (BIP37)
MY_RELAY = 1

MAX_LOCATOR_SZ = 101
MAX_BLOCK_BASE_SIZE = 1000000
MAX_BLOOM_FILTER_SIZE = 36000
MAX_BLOOM_HASH_FUNCS = 50

# 1 BCH in satoshis
LOTUS = 1000000
COIN = LOTUS
MAX_MONEY = 2100000000 * COIN

# Maximum length of incoming protocol messages
MAX_PROTOCOL_MESSAGE_LENGTH = 2 * 1024 * 1024
MAX_HEADERS_RESULTS = 2000  # Number of headers sent in one getheaders result
MAX_INV_SIZE = 50000  # Maximum number of entries in an 'inv' protocol message

NODE_NETWORK = (1 << 0)
NODE_GETUTXO = (1 << 1)
NODE_BLOOM = (1 << 2)
# NODE_WITNESS = (1 << 3)
# NODE_XTHIN = (1 << 4) # removed in v0.22.12
NODE_COMPACT_FILTERS = (1 << 6)
NODE_NETWORK_LIMITED = (1 << 10)
NODE_AVALANCHE = (1 << 24)

MSG_TX = 1
MSG_BLOCK = 2
MSG_FILTERED_BLOCK = 3
MSG_CMPCT_BLOCK = 4
MSG_AVA_PROOF = 0x1f000001
MSG_TYPE_MASK = 0xffffffff >> 2

FILTER_TYPE_BASIC = 0

# Serialization/deserialization tools


def sha256(s):
    return hashlib.new('sha256', s).digest()


def hash256(s):
    return sha256(sha256(s))


def ser_compact_size(size):
    r = b""
    if size < 253:
        r = struct.pack("B", size)
    elif size < 0x10000:
        r = struct.pack("<BH", 253, size)
    elif size < 0x100000000:
        r = struct.pack("<BI", 254, size)
    else:
        r = struct.pack("<BQ", 255, size)
    return r


def deser_compact_size(f):
    nit = struct.unpack("<B", f.read(1))[0]
    if nit == 253:
        nit = struct.unpack("<H", f.read(2))[0]
    elif nit == 254:
        nit = struct.unpack("<I", f.read(4))[0]
    elif nit == 255:
        nit = struct.unpack("<Q", f.read(8))[0]
    return nit


def deser_string(f):
    nit = deser_compact_size(f)
    return f.read(nit)


def ser_string(s):
    return ser_compact_size(len(s)) + s


def deser_uint256(f):
    r = 0
    for i in range(8):
        t = struct.unpack("<I", f.read(4))[0]
        r += t << (i * 32)
    return r


def ser_uint256(u):
    rs = b""
    for _ in range(8):
        rs += struct.pack("<I", u & 0xFFFFFFFF)
        u >>= 32
    return rs


def uint256_from_str(s):
    r = 0
    t = struct.unpack("<IIIIIIII", s[:32])
    for i in range(8):
        r += t[i] << (i * 32)
    return r


def uint256_from_compact(c):
    nbytes = (c >> 24) & 0xFF
    v = (c & 0xFFFFFF) << (8 * (nbytes - 3))
    return v


# deser_function_name: Allow for an alternate deserialization function on the
# entries in the vector.
def deser_vector(f, c, deser_function_name=None):
    nit = deser_compact_size(f)
    r = []
    for _ in range(nit):
        t = c()
        if deser_function_name:
            getattr(t, deser_function_name)(f)
        else:
            t.deserialize(f)
        r.append(t)
    return r


# ser_function_name: Allow for an alternate serialization function on the
# entries in the vector.
def ser_vector(v, ser_function_name=None):
    r = ser_compact_size(len(v))
    for i in v:
        if ser_function_name:
            r += getattr(i, ser_function_name)()
        else:
            r += i.serialize()
    return r


def deser_uint256_vector(f):
    nit = deser_compact_size(f)
    r = []
    for _ in range(nit):
        t = deser_uint256(f)
        r.append(t)
    return r


def ser_uint256_vector(v):
    r = ser_compact_size(len(v))
    for i in v:
        r += ser_uint256(i)
    return r


def deser_string_vector(f):
    nit = deser_compact_size(f)
    r = []
    for _ in range(nit):
        t = deser_string(f)
        r.append(t)
    return r


def ser_string_vector(v):
    r = ser_compact_size(len(v))
    for sv in v:
        r += ser_string(sv)
    return r


def FromHex(obj, hex_string):
    """Deserialize from a hex string representation (eg from RPC)"""
    obj.deserialize(BytesIO(hex_str_to_bytes(hex_string)))
    return obj


def ToHex(obj):
    """Convert a binary-serializable object to hex
    (eg for submission via RPC)"""
    return obj.serialize().hex()


# Calculate merkle root given a vector of hashes
def get_merkle_root(hashes):
    if not hashes:
        return bytes(32), 0
    num_layers = 1
    while len(hashes) > 1:
        num_layers += 1
        newhashes = []
        for i in range(0, len(hashes), 2):
            if i + 1 < len(hashes):
                other = hashes[i + 1]
            else:
                other = bytes(32)
            newhashes.append(hash256(hashes[i] + other))
        hashes = newhashes
    return hashes[0], num_layers


# Objects that map to lotusd objects, which can be serialized/deserialized

class CAddress:
    __slots__ = ("net", "ip", "nServices", "port", "time")

    # see https://github.com/bitcoin/bips/blob/master/bip-0155.mediawiki
    NET_IPV4 = 1

    ADDRV2_NET_NAME = {
        NET_IPV4: "IPv4"
    }

    ADDRV2_ADDRESS_LENGTH = {
        NET_IPV4: 4
    }

    def __init__(self):
        self.time = 0
        self.nServices = 1
        self.net = self.NET_IPV4
        self.ip = "0.0.0.0"
        self.port = 0

    def deserialize(self, f, *, with_time=True):
        """Deserialize from addrv1 format (pre-BIP155)"""
        if with_time:
            # VERSION messages serialize CAddress objects without time
            self.time = struct.unpack("<I", f.read(4))[0]
        self.nServices = struct.unpack("<Q", f.read(8))[0]
        # We only support IPv4 which means skip 12 bytes and read the next 4 as
        # IPv4 address.
        f.read(12)
        self.net = self.NET_IPV4
        self.ip = socket.inet_ntoa(f.read(4))
        self.port = struct.unpack(">H", f.read(2))[0]

    def serialize(self, *, with_time=True):
        """Serialize in addrv1 format (pre-BIP155)"""
        assert self.net == self.NET_IPV4
        r = b""
        if with_time:
            # VERSION messages serialize CAddress objects without time
            r += struct.pack("<I", self.time)
        r += struct.pack("<Q", self.nServices)
        r += b"\x00" * 10 + b"\xff" * 2
        r += socket.inet_aton(self.ip)
        r += struct.pack(">H", self.port)
        return r

    def deserialize_v2(self, f):
        """Deserialize from addrv2 format (BIP155)"""
        self.time = struct.unpack("<I", f.read(4))[0]

        self.nServices = deser_compact_size(f)

        self.net = struct.unpack("B", f.read(1))[0]
        assert self.net == self.NET_IPV4

        address_length = deser_compact_size(f)
        assert address_length == self.ADDRV2_ADDRESS_LENGTH[self.net]

        self.ip = socket.inet_ntoa(f.read(4))

        self.port = struct.unpack(">H", f.read(2))[0]

    def serialize_v2(self):
        """Serialize in addrv2 format (BIP155)"""
        assert self.net == self.NET_IPV4
        r = b""
        r += struct.pack("<I", self.time)
        r += ser_compact_size(self.nServices)
        r += struct.pack("B", self.net)
        r += ser_compact_size(self.ADDRV2_ADDRESS_LENGTH[self.net])
        r += socket.inet_aton(self.ip)
        r += struct.pack(">H", self.port)
        return r

    def __repr__(self):
        return ("CAddress(nServices=%i net=%s addr=%s port=%i)"
                % (self.nServices, self.ADDRV2_NET_NAME[self.net], self.ip, self.port))


class CInv:
    __slots__ = ("hash", "type")

    typemap = {
        0: "Error",
        MSG_TX: "TX",
        MSG_BLOCK: "Block",
        MSG_FILTERED_BLOCK: "filtered Block",
        MSG_CMPCT_BLOCK: "CompactBlock",
        MSG_AVA_PROOF: "avalanche proof",
    }

    def __init__(self, t=0, h=0):
        self.type = t
        self.hash = h

    def deserialize(self, f):
        self.type = struct.unpack("<i", f.read(4))[0]
        self.hash = deser_uint256(f)

    def serialize(self):
        r = b""
        r += struct.pack("<i", self.type)
        r += ser_uint256(self.hash)
        return r

    def __repr__(self):
        return "CInv(type={} hash={:064x})".format(
            self.typemap[self.type], self.hash)

    def __eq__(self, other):
        return isinstance(
            other, CInv) and self.hash == other.hash and self.type == other.type


class CBlockLocator:
    __slots__ = ("nVersion", "vHave")

    def __init__(self):
        self.nVersion = MY_VERSION
        self.vHave = []

    def deserialize(self, f):
        self.nVersion = struct.unpack("<i", f.read(4))[0]
        self.vHave = deser_uint256_vector(f)

    def serialize(self):
        r = b""
        r += struct.pack("<i", self.nVersion)
        r += ser_uint256_vector(self.vHave)
        return r

    def __repr__(self):
        return "CBlockLocator(nVersion={} vHave={})".format(
            self.nVersion, repr(self.vHave))


class COutPoint:
    __slots__ = ("hash", "n")

    def __init__(self, hash=0, n=0):
        self.hash = hash
        self.n = n

    def deserialize(self, f):
        self.hash = deser_uint256(f)
        self.n = struct.unpack("<I", f.read(4))[0]

    def serialize(self):
        r = b""
        r += ser_uint256(self.hash)
        r += struct.pack("<I", self.n)
        return r

    def __repr__(self):
        return "COutPoint(hash={:064x} n={})".format(self.hash, self.n)


class CTxIn:
    __slots__ = ("nSequence", "prevout", "scriptSig")

    def __init__(self, outpoint=None, scriptSig=b"", nSequence=0):
        if outpoint is None:
            self.prevout = COutPoint()
        else:
            self.prevout = outpoint
        self.scriptSig = scriptSig
        self.nSequence = nSequence

    def deserialize(self, f):
        self.prevout = COutPoint()
        self.prevout.deserialize(f)
        self.scriptSig = deser_string(f)
        self.nSequence = struct.unpack("<I", f.read(4))[0]

    def serialize(self):
        r = b""
        r += self.prevout.serialize()
        r += ser_string(self.scriptSig)
        r += struct.pack("<I", self.nSequence)
        return r

    def __repr__(self):
        return "CTxIn(prevout={} scriptSig={} nSequence={})".format(
            repr(self.prevout), self.scriptSig.hex(), self.nSequence)


class CTxOut:
    __slots__ = ("nValue", "scriptPubKey")

    def __init__(self, nValue=0, scriptPubKey=b""):
        self.nValue = nValue
        self.scriptPubKey = scriptPubKey

    def deserialize(self, f):
        self.nValue = struct.unpack("<q", f.read(8))[0]
        self.scriptPubKey = deser_string(f)

    def serialize(self):
        r = b""
        r += struct.pack("<q", self.nValue)
        r += ser_string(self.scriptPubKey)
        return r

    def __repr__(self):
        return "CTxOut(nValue={}.{:06d} scriptPubKey={})".format(
            self.nValue // COIN, self.nValue % COIN, self.scriptPubKey.hex())


class CTransaction:
    __slots__ = (
        "txhash",
        "txhash_hex",
        "txid",
        "txid_hex",
        "nLockTime",
        "nVersion",
        "vin",
        "vout",
    )

    def __init__(self, tx=None):
        if tx is None:
            self.nVersion = 1
            self.vin = []
            self.vout = []
            self.nLockTime = 0
            self.txhash = None
            self.txhash_hex = None
            self.txid = None
            self.txid_hex = None
        else:
            self.nVersion = tx.nVersion
            self.vin = copy.deepcopy(tx.vin)
            self.vout = copy.deepcopy(tx.vout)
            self.nLockTime = tx.nLockTime
            self.txhash = tx.txhash
            self.txhash_hex = tx.txhash_hex
            self.txid = tx.txid
            self.txid_hex = tx.txid_hex

    def deserialize(self, f):
        self.nVersion = struct.unpack("<i", f.read(4))[0]
        self.vin = deser_vector(f, CTxIn)
        self.vout = deser_vector(f, CTxOut)
        self.nLockTime = struct.unpack("<I", f.read(4))[0]
        self.txhash = None
        self.txhash_hex = None
        self.txid = None
        self.txid_hex = None

    def billable_size(self):
        """
        Returns the size used for billing the against the transaction
        """
        return len(self.serialize())

    def serialize(self):
        r = b""
        r += struct.pack("<i", self.nVersion)
        r += ser_vector(self.vin)
        r += ser_vector(self.vout)
        r += struct.pack("<I", self.nLockTime)
        return r

    # Recalculate the txid
    def rehash(self):
        self.calc_txhash()
        self.calc_txid()

    def calc_txhash(self):
        txhash_bytes = hash256(self.serialize())
        self.txhash_hex = txhash_bytes[::-1].hex()
        self.txhash = uint256_from_str(txhash_bytes)

    def calc_txid(self):
        r = bytearray()
        r += self.nVersion.to_bytes(4, 'little')
        input_merkle_root, num_layers = self.input_merkle_root()
        r += input_merkle_root
        r += num_layers.to_bytes(1, 'little')
        output_merkle_root, num_layers = self.output_merkle_root()
        r += output_merkle_root
        r += num_layers.to_bytes(1, 'little')
        r += self.nLockTime.to_bytes(4, 'little')
        txid_bytes = hash256(r)
        self.txid_hex = txid_bytes[::-1].hex()
        self.txid = uint256_from_str(txid_bytes)
    
    def is_coinbase(self):
        return self.vin[0].prevout.hash == 0

    def input_merkle_root(self):
        hashes = []
        for tx_input in self.vin:
            tx_input_ser = bytearray()
            tx_input_ser += tx_input.prevout.serialize()
            tx_input_ser += tx_input.nSequence.to_bytes(4, 'little')
            hashes.append(hash256(tx_input_ser))
        return get_merkle_root(hashes)

    def output_merkle_root(self):
        hashes = []
        for tx_output in self.vout:
            hashes.append(hash256(tx_output.serialize()))
        return get_merkle_root(hashes)

    def get_id(self):
        # For now, just forward the hash.
        self.calc_txid()
        return self.txid

    def is_valid(self):
        self.calc_sha256()
        for tout in self.vout:
            if tout.nValue < 0 or tout.nValue > MAX_MONEY:
                return False
        return True

    def __repr__(self):
        return "CTransaction(nVersion={} vin={} vout={} nLockTime={})".format(
            self.nVersion, repr(self.vin), repr(self.vout), self.nLockTime)


class CBlockHeader:
    __slots__ = (
        "hashPrevBlock",
        "nBits",
        "nTime",
        "nReserved",
        "nNonce",
        "nHeaderVersion",
        "nSize",
        "nHeight",
        "hashEpochBlock",
        "hashMerkleRoot",
        "hashExtendedMetadata",
        "hash",
        "sha256",
    )

    def __init__(self, header=None):
        if header is None:
            self.set_null()
        else:
            self.hashPrevBlock = header.hashPrevBlock
            self.nBits = header.nBits
            self.nTime = header.nTime
            self.nReserved = header.nReserved
            self.nNonce = header.nNonce
            self.nHeaderVersion = header.nHeaderVersion
            self.nSize = header.nSize
            self.nHeight = header.nHeight
            self.hashEpochBlock = header.hashEpochBlock
            self.hashMerkleRoot = header.hashMerkleRoot
            self.hashExtendedMetadata = header.hashExtendedMetadata
            self.sha256 = header.sha256
            self.hash = header.hash
            self.calc_sha256()

    def set_null(self):
        self.hashPrevBlock = 0
        self.nBits = 0
        self.nTime = 0
        self.nReserved = 0
        self.nNonce = 0
        self.nHeaderVersion = 1
        self.nSize = 0
        self.nHeight = 0
        self.hashEpochBlock = 0
        self.hashMerkleRoot = 0
        self.hashExtendedMetadata = 0
        self.sha256 = None
        self.hash = None

    def deserialize(self, f):
        self.hashPrevBlock = deser_uint256(f)
        self.nBits = int.from_bytes(f.read(4), 'little')
        self.nTime = int.from_bytes(f.read(6), 'little')
        self.nReserved = int.from_bytes(f.read(2), 'little')
        self.nNonce = int.from_bytes(f.read(8), 'little')
        self.nHeaderVersion = int.from_bytes(f.read(1), 'little')
        self.nSize = int.from_bytes(f.read(7), 'little')
        self.nHeight = int.from_bytes(f.read(4), 'little')
        self.hashEpochBlock = deser_uint256(f)
        self.hashMerkleRoot = deser_uint256(f)
        self.hashExtendedMetadata = deser_uint256(f)
        self.sha256 = None
        self.hash = None

    def serialize(self):
        r = bytearray()
        r += ser_uint256(self.hashPrevBlock)
        r += self.nBits.to_bytes(4, 'little')
        r += self.nTime.to_bytes(6, 'little')
        r += self.nReserved.to_bytes(2, 'little')
        r += self.nNonce.to_bytes(8, 'little')
        r += self.nHeaderVersion.to_bytes(1, 'little')
        r += self.nSize.to_bytes(7, 'little')
        r += self.nHeight.to_bytes(4, 'little')
        r += ser_uint256(self.hashEpochBlock)
        r += ser_uint256(self.hashMerkleRoot)
        r += ser_uint256(self.hashExtendedMetadata)
        return bytes(r)

    def calc_sha256(self):
        if self.sha256 is None:
            layer3 = bytearray()
            layer3 += self.nHeaderVersion.to_bytes(1, 'little')
            layer3 += self.nSize.to_bytes(7, 'little')
            layer3 += self.nHeight.to_bytes(4, 'little')
            layer3 += ser_uint256(self.hashEpochBlock)
            layer3 += ser_uint256(self.hashMerkleRoot)
            layer3 += ser_uint256(self.hashExtendedMetadata)
            layer2 = bytearray()
            layer2 += self.nBits.to_bytes(4, 'little')
            layer2 += self.nTime.to_bytes(6, 'little')
            layer2 += self.nReserved.to_bytes(2, 'little')
            layer2 += self.nNonce.to_bytes(8, 'little')
            layer2 += hashlib.sha256(layer3).digest()
            layer1 = bytearray()
            layer1 += ser_uint256(self.hashPrevBlock)
            layer1 += hashlib.sha256(layer2).digest()
            hash_bytes = sha256(layer1)
            self.sha256 = uint256_from_str(hash_bytes)
            self.hash = hash_bytes[::-1].hex()

    def rehash(self):
        self.sha256 = None
        self.calc_sha256()
        return self.sha256

    def __repr__(self):
        return (
            "CBlockHeader(hashPrevBlock={:064x} nBits={:08x} nTime={} "
            "nReserved={} nNonce={} nHeaderVersion={} nSize={} nHeight={} "
            "hashEpochBlock={:064x} hashMerkleRoot={:064x} "
            "hashExtendedMetadata={:064x})".format(
                self.hashPrevBlock, self.nBits, self.nTime, self.nReserved,
                self.nNonce, self.nHeaderVersion, self.nSize, self.nHeight,
                self.hashEpochBlock, self.hashMerkleRoot,
                self.hashExtendedMetadata,
            )
        )


BLOCK_HEADER_SIZE = len(CBlockHeader().serialize())
assert_equal(BLOCK_HEADER_SIZE, 160)


class CBlock(CBlockHeader):
    __slots__ = ("vMetadata", "vtx",)

    def __init__(self, header=None):
        super().__init__(header)
        self.vMetadata = []
        self.vtx = []

    def deserialize(self, f):
        super().deserialize(f)
        self.vMetadata = deser_vector(f, CBlockMetadataField)
        self.vtx = deser_vector(f, CTransaction)

    def serialize(self):
        r = b""
        r += super().serialize()
        r += ser_vector(self.vMetadata)
        r += ser_vector(self.vtx)
        return r

    def update_size(self):
        self.nSize = len(self.serialize())

    def rehash_extended_metadata(self):
        self.hashExtendedMetadata = uint256_from_str(
            hash256(ser_vector(self.vMetadata)))

    def calc_merkle_root(self):
        hashes = []
        for tx in self.vtx:
            tx.rehash()
            hashes.append(hash256(ser_uint256(tx.txhash) + ser_uint256(tx.txid)))
        return uint256_from_str(get_merkle_root(hashes)[0])

    def is_valid(self):
        self.calc_sha256()
        target = uint256_from_compact(self.nBits)
        if self.sha256 > target:
            return False
        for tx in self.vtx:
            if not tx.is_valid():
                return False
        if self.calc_merkle_root() != self.hashMerkleRoot:
            return False
        return True

    def solve(self):
        self.rehash()
        target = uint256_from_compact(self.nBits)
        while self.sha256 > target:
            self.nNonce += 1
            self.rehash()

    def __repr__(self):
        return "CBlock(nHeaderVersion={} hashPrevBlock={:064x} hashMerkleRoot={:064x} nTime={} nBits={:08x} nNonce={:08x} vtx={})".format(
            self.nHeaderVersion, self.hashPrevBlock, self.hashMerkleRoot,
            self.nTime, self.nBits, self.nNonce, repr(self.vtx))


class CBlockMetadataField:
    __slots__ = ("fieldId", "data")

    def __init__(self, fieldId, data):
        self.fieldId = fieldId
        self.data = data

    def deserialize(self, f):
        self.fieldId = int.from_bytes(f.read(4), 'little')
        self.data = deser_string(f)

    def serialize(self):
        r = bytearray()
        r += self.fieldId.to_bytes(4, 'little')
        r += ser_string(self.data)
        return r


class PrefilledTransaction:
    __slots__ = ("index", "tx")

    def __init__(self, index=0, tx=None):
        self.index = index
        self.tx = tx

    def deserialize(self, f):
        self.index = deser_compact_size(f)
        self.tx = CTransaction()
        self.tx.deserialize(f)

    def serialize(self):
        r = b""
        r += ser_compact_size(self.index)
        r += self.tx.serialize()
        return r

    def __repr__(self):
        return "PrefilledTransaction(index={}, tx={})".format(
            self.index, repr(self.tx))


# This is what we send on the wire, in a cmpctblock message.
class P2PHeaderAndShortIDs:
    __slots__ = ("header", "vMetadata", "nonce", "prefilled_txn",
                 "prefilled_txn_length", "shortids", "shortids_length")

    def __init__(self):
        self.header = CBlockHeader()
        self.vMetadata = []
        self.nonce = 0
        self.shortids_length = 0
        self.shortids = []
        self.prefilled_txn_length = 0
        self.prefilled_txn = []

    def deserialize(self, f):
        self.header.deserialize(f)
        self.vMetadata = deser_vector(f, CBlockMetadataField)
        self.nonce = struct.unpack("<Q", f.read(8))[0]
        self.shortids_length = deser_compact_size(f)
        for _ in range(self.shortids_length):
            # shortids are defined to be 6 bytes in the spec, so append
            # two zero bytes and read it in as an 8-byte number
            self.shortids.append(
                struct.unpack("<Q", f.read(6) + b'\x00\x00')[0])
        self.prefilled_txn = deser_vector(f, PrefilledTransaction)
        self.prefilled_txn_length = len(self.prefilled_txn)

    def serialize(self):
        r = b""
        r += self.header.serialize()
        r += ser_vector(self.vMetadata)
        r += struct.pack("<Q", self.nonce)
        r += ser_compact_size(self.shortids_length)
        for x in self.shortids:
            # We only want the first 6 bytes
            r += struct.pack("<Q", x)[0:6]
        r += ser_vector(self.prefilled_txn)
        return r

    def __repr__(self):
        return "P2PHeaderAndShortIDs(header={}, vMetadata={}, nonce={}, shortids_length={}, shortids={}, prefilled_txn_length={}, prefilledtxn={}".format(
            repr(self.header), repr(self.vMetadata), self.nonce, self.shortids_length,
            repr(self.shortids), self.prefilled_txn_length,
            repr(self.prefilled_txn))


def calculate_shortid(k0, k1, tx_hash):
    """Calculate the BIP 152-compact blocks shortid for a given
    transaction hash"""
    expected_shortid = siphash256(k0, k1, tx_hash)
    expected_shortid &= 0x0000ffffffffffff
    return expected_shortid


# This version gets rid of the array lengths, and reinterprets the differential
# encoding into indices that can be used for lookup.
class HeaderAndShortIDs:
    __slots__ = ("header", "nonce", "prefilled_txn", "shortids")

    def __init__(self, p2pheaders_and_shortids=None):
        self.header = CBlockHeader()
        self.nonce = 0
        self.shortids = []
        self.prefilled_txn = []

        if p2pheaders_and_shortids is not None:
            self.header = p2pheaders_and_shortids.header
            self.nonce = p2pheaders_and_shortids.nonce
            self.shortids = p2pheaders_and_shortids.shortids
            last_index = -1
            for x in p2pheaders_and_shortids.prefilled_txn:
                self.prefilled_txn.append(
                    PrefilledTransaction(x.index + last_index + 1, x.tx))
                last_index = self.prefilled_txn[-1].index

    def to_p2p(self):
        ret = P2PHeaderAndShortIDs()
        ret.header = self.header
        ret.nonce = self.nonce
        ret.shortids_length = len(self.shortids)
        ret.shortids = self.shortids
        ret.prefilled_txn_length = len(self.prefilled_txn)
        ret.prefilled_txn = []
        last_index = -1
        for x in self.prefilled_txn:
            ret.prefilled_txn.append(
                PrefilledTransaction(x.index - last_index - 1, x.tx))
            last_index = x.index
        return ret

    def get_siphash_keys(self):
        header_nonce = self.header.serialize()
        header_nonce += struct.pack("<Q", self.nonce)
        hash_header_nonce_as_str = sha256(header_nonce)
        key0 = struct.unpack("<Q", hash_header_nonce_as_str[0:8])[0]
        key1 = struct.unpack("<Q", hash_header_nonce_as_str[8:16])[0]
        return [key0, key1]

    # Version 2 compact blocks use wtxid in shortids (rather than txid)
    def initialize_from_block(self, block, nonce=0, prefill_list=None):
        if prefill_list is None:
            prefill_list = [0]
        self.header = CBlockHeader(block)
        self.nonce = nonce
        self.prefilled_txn = [PrefilledTransaction(i, block.vtx[i])
                              for i in prefill_list]
        self.shortids = []
        [k0, k1] = self.get_siphash_keys()
        for i in range(len(block.vtx)):
            if i not in prefill_list:
                tx_hash = block.vtx[i].txhash
                self.shortids.append(calculate_shortid(k0, k1, tx_hash))

    def __repr__(self):
        return "HeaderAndShortIDs(header={}, nonce={}, shortids={}, prefilledtxn={}".format(
            repr(self.header), self.nonce, repr(self.shortids),
            repr(self.prefilled_txn))


class BlockTransactionsRequest:
    __slots__ = ("blockhash", "indexes")

    def __init__(self, blockhash=0, indexes=None):
        self.blockhash = blockhash
        self.indexes = indexes if indexes is not None else []

    def deserialize(self, f):
        self.blockhash = deser_uint256(f)
        indexes_length = deser_compact_size(f)
        for _ in range(indexes_length):
            self.indexes.append(deser_compact_size(f))

    def serialize(self):
        r = b""
        r += ser_uint256(self.blockhash)
        r += ser_compact_size(len(self.indexes))
        for x in self.indexes:
            r += ser_compact_size(x)
        return r

    # helper to set the differentially encoded indexes from absolute ones
    def from_absolute(self, absolute_indexes):
        self.indexes = []
        last_index = -1
        for x in absolute_indexes:
            self.indexes.append(x - last_index - 1)
            last_index = x

    def to_absolute(self):
        absolute_indexes = []
        last_index = -1
        for x in self.indexes:
            absolute_indexes.append(x + last_index + 1)
            last_index = absolute_indexes[-1]
        return absolute_indexes

    def __repr__(self):
        return "BlockTransactionsRequest(hash={:064x} indexes={})".format(
            self.blockhash, repr(self.indexes))


class BlockTransactions:
    __slots__ = ("blockhash", "transactions")

    def __init__(self, blockhash=0, transactions=None):
        self.blockhash = blockhash
        self.transactions = transactions if transactions is not None else []

    def deserialize(self, f):
        self.blockhash = deser_uint256(f)
        self.transactions = deser_vector(f, CTransaction)

    def serialize(self):
        r = b""
        r += ser_uint256(self.blockhash)
        r += ser_vector(self.transactions)
        return r

    def __repr__(self):
        return "BlockTransactions(hash={:064x} transactions={})".format(
            self.blockhash, repr(self.transactions))


class AvalancheStake:
    def __init__(self, utxo=None, amount=0, height=0,
                 pubkey=b"", is_coinbase=False):
        self.utxo: COutPoint = utxo or COutPoint()
        self.amount: int = amount
        """Amount in satoshis (int64)"""
        self.height: int = height
        """Block height containing this utxo (uint32)"""
        self.pubkey: bytes = pubkey
        """Public key"""

        self.is_coinbase: bool = is_coinbase

    def deserialize(self, f):
        self.utxo = COutPoint()
        self.utxo.deserialize(f)
        self.amount = struct.unpack("<q", f.read(8))[0]
        height_ser = struct.unpack("<I", f.read(4))[0]
        self.is_coinbase = bool(height_ser & 1)
        self.height = height_ser >> 1
        self.pubkey = deser_string(f)

    def serialize(self) -> bytes:
        r = self.utxo.serialize()
        height_ser = self.height << 1 | int(self.is_coinbase)
        r += struct.pack('<q', self.amount)
        r += struct.pack('<I', height_ser)
        r += ser_compact_size(len(self.pubkey))
        r += self.pubkey
        return r

    def __repr__(self):
        return f"AvalancheStake(utxo={self.utxo}, amount={self.amount}," \
               f" height={self.height}, " \
               f"pubkey={self.pubkey.hex()})"


class AvalancheSignedStake:
    def __init__(self, stake=None, sig=b""):
        self.stake: AvalancheStake = stake or AvalancheStake()
        self.sig: bytes = sig
        """Signature for this stake, bytes of length 64"""

    def deserialize(self, f):
        self.stake = AvalancheStake()
        self.stake.deserialize(f)
        self.sig = f.read(64)

    def serialize(self) -> bytes:
        return self.stake.serialize() + self.sig


class AvalancheProof:
    __slots__ = (
        "sequence",
        "expiration",
        "master",
        "stakes",
        "payout_script",
        "signature",
        "limited_proofid",
        "proofid")

    def __init__(self, sequence=0, expiration=0,
                 master=b"", signed_stakes=None, payout_script=b"", signature=b""):
        self.sequence: int = sequence
        self.expiration: int = expiration
        self.master: bytes = master

        self.stakes: List[AvalancheSignedStake] = signed_stakes or [
            AvalancheSignedStake()]

        self.payout_script = payout_script
        self.signature = signature

        self.limited_proofid: int = None
        self.proofid: int = None
        self.compute_proof_id()

    def compute_proof_id(self):
        """Compute Bitcoin's 256-bit hash (double SHA-256) of the
        serialized proof data.
        """
        ss = struct.pack("<Qq", self.sequence, self.expiration)
        ss += ser_string(self.payout_script)
        ss += ser_compact_size(len(self.stakes))
        # Use unsigned stakes
        for s in self.stakes:
            ss += s.stake.serialize()
        h = hash256(ss)
        self.limited_proofid = uint256_from_str(h)
        h += ser_string(self.master)
        h = hash256(h)
        # make it an int, for comparing with Delegation.proofid
        self.proofid = uint256_from_str(h)

    def deserialize(self, f):
        self.sequence = struct.unpack("<Q", f.read(8))[0]
        self.expiration = struct.unpack("<q", f.read(8))[0]
        self.master = deser_string(f)
        self.stakes = deser_vector(f, AvalancheSignedStake)
        self.payout_script = deser_string(f)
        self.signature = f.read(64)
        self.compute_proof_id()

    def serialize(self):
        r = b""
        r += struct.pack("<Q", self.sequence)
        r += struct.pack("<q", self.expiration)
        r += ser_string(self.master)
        r += ser_vector(self.stakes)
        r += ser_string(self.payout_script)
        r += self.signature
        return r

    def __repr__(self):
        return f"AvalancheProof(sequence={self.sequence}, " \
               f"expiration={self.expiration}, " \
               f"master={self.master.hex()}, " \
               f"payout_script={self.payout_script.hex()}, " \
               f"signature={b64encode(self.signature)}, " \
               f"stakes={self.stakes})"


class LegacyAvalancheProof(AvalancheProof):
    def __init__(self, sequence=0, expiration=0,
                 master=b"", signed_stakes=None):
        super().__init__(sequence, expiration, master, signed_stakes)

    def compute_proof_id(self):
        """Compute Bitcoin's 256-bit hash (double SHA-256) of the
        serialized proof data.
        """
        ss = struct.pack("<Qq", self.sequence, self.expiration)
        ss += ser_compact_size(len(self.stakes))
        # Use unsigned stakes
        for s in self.stakes:
            ss += s.stake.serialize()
        h = hash256(ss)
        self.limited_proofid = uint256_from_str(h)
        h += ser_string(self.master)
        h = hash256(h)
        # make it an int, for comparing with Delegation.proofid
        self.proofid = uint256_from_str(h)

    def deserialize(self, f):
        self.sequence = struct.unpack("<Q", f.read(8))[0]
        self.expiration = struct.unpack("<q", f.read(8))[0]
        self.master = deser_string(f)
        self.stakes = deser_vector(f, AvalancheSignedStake)
        self.compute_proof_id()

    def serialize(self):
        r = b""
        r += struct.pack("<Q", self.sequence)
        r += struct.pack("<q", self.expiration)
        r += ser_string(self.master)
        r += ser_vector(self.stakes)
        return r

    def __repr__(self):
        return f"LegacyAvalancheProof(sequence={self.sequence}, " \
               f"expiration={self.expiration}, " \
               f"master={self.master.hex()}, " \
               f"stakes={self.stakes})"


class AvalanchePoll():
    __slots__ = ("round", "invs")

    def __init__(self, round=0, invs=None):
        self.round = round
        self.invs = invs if invs is not None else []

    def deserialize(self, f):
        self.round = struct.unpack("<q", f.read(8))[0]
        self.invs = deser_vector(f, CInv)

    def serialize(self):
        r = b""
        r += struct.pack("<q", self.round)
        r += ser_vector(self.invs)
        return r

    def __repr__(self):
        return "AvalanchePoll(round={}, invs={})".format(
            self.round, repr(self.invs))


class AvalancheVoteError(IntEnum):
    ACCEPTED = 0
    INVALID = 1
    PARKED = 2
    FORK = 3
    UNKNOWN = -1
    MISSING = -2
    PENDING = -3


class AvalancheVote():
    __slots__ = ("error", "hash")

    def __init__(self, e=0, h=0):
        self.error = e
        self.hash = h

    def deserialize(self, f):
        self.error = struct.unpack("<i", f.read(4))[0]
        self.hash = deser_uint256(f)

    def serialize(self):
        r = b""
        r += struct.pack("<i", self.error)
        r += ser_uint256(self.hash)
        return r

    def __repr__(self):
        return "AvalancheVote(error={}, hash={:064x})".format(
            self.error, self.hash)


class AvalancheResponse():
    __slots__ = ("round", "cooldown", "votes")

    def __init__(self, round=0, cooldown=0, votes=None):
        self.round = round
        self.cooldown = cooldown
        self.votes = votes if votes is not None else []

    def deserialize(self, f):
        self.round = struct.unpack("<q", f.read(8))[0]
        self.cooldown = struct.unpack("<i", f.read(4))[0]
        self.votes = deser_vector(f, AvalancheVote)

    def serialize(self):
        r = b""
        r += struct.pack("<q", self.round)
        r += struct.pack("<i", self.cooldown)
        r += ser_vector(self.votes)
        return r

    def get_hash(self):
        return hash256(self.serialize())

    def __repr__(self):
        return "AvalancheResponse(round={}, cooldown={}, votes={})".format(
            self.round, self.cooldown, repr(self.votes))


class TCPAvalancheResponse():
    __slots__ = ("response", "sig")

    def __init__(self, response=AvalancheResponse(), sig=b"\0" * 64):
        self.response = response
        self.sig = sig

    def deserialize(self, f):
        self.response.deserialize(f)
        self.sig = f.read(64)

    def serialize(self):
        r = b""
        r += self.response.serialize()
        r += self.sig
        return r

    def __repr__(self):
        return "TCPAvalancheResponse(response={}, sig={})".format(
            repr(self.response), self.sig)


class AvalancheDelegationLevel:
    __slots__ = ("pubkey", "sig")

    def __init__(self, pubkey=b"", sig=b"\0" * 64):
        self.pubkey = pubkey
        self.sig = sig

    def deserialize(self, f):
        self.pubkey = deser_string(f)
        self.sig = f.read(64)

    def serialize(self):
        r = b""
        r += ser_string(self.pubkey)
        r += self.sig
        return r

    def __repr__(self):
        return "AvalancheDelegationLevel(pubkey={}, sig={})".format(
            self.pubkey.hex(), self.sig)


class AvalancheDelegation:
    __slots__ = ("limited_proofid", "proof_master", "proofid", "levels")

    def __init__(self, limited_proofid=0,
                 proof_master=b"", levels=None):
        self.limited_proofid: int = limited_proofid
        self.proof_master: bytes = proof_master
        self.levels: List[AvalancheDelegationLevel] = levels or []
        self.proofid: int = self.compute_proofid()

    def compute_proofid(self) -> int:
        return uint256_from_str(hash256(
            ser_uint256(self.limited_proofid) + ser_string(self.proof_master)))

    def deserialize(self, f):
        self.limited_proofid = deser_uint256(f)
        self.proof_master = deser_string(f)
        self.levels = deser_vector(f, AvalancheDelegationLevel)

        self.proofid = self.compute_proofid()

    def serialize(self):
        r = b""
        r += ser_uint256(self.limited_proofid)
        r += ser_string(self.proof_master)
        r += ser_vector(self.levels)
        return r

    def __repr__(self):
        return f"AvalancheDelegation(limitedProofId={self.limited_proofid:064x}, " \
               f"proofMaster={self.proof_master.hex()}, proofid={self.proofid:064x}, " \
               f"levels={self.levels})"

    def getid(self):
        h = ser_uint256(self.proofid)
        for level in self.levels:
            h = hash256(h + ser_string(level.pubkey))
        return h


class AvalancheHello():
    __slots__ = ("delegation", "sig")

    def __init__(self, delegation=AvalancheDelegation(), sig=b"\0" * 64):
        self.delegation = delegation
        self.sig = sig

    def deserialize(self, f):
        self.delegation.deserialize(f)
        self.sig = f.read(64)

    def serialize(self):
        r = b""
        r += self.delegation.serialize()
        r += self.sig
        return r

    def __repr__(self):
        return "AvalancheHello(delegation={}, sig={})".format(
            repr(self.delegation), self.sig)

    def get_sighash(self, node):
        b = self.delegation.getid()
        b += struct.pack("<Q", node.remote_nonce)
        b += struct.pack("<Q", node.local_nonce)
        b += struct.pack("<Q", node.remote_extra_entropy)
        b += struct.pack("<Q", node.local_extra_entropy)
        return hash256(b)


class CPartialMerkleTree:
    __slots__ = ("nTransactions", "vBits", "vHash")

    def __init__(self):
        self.nTransactions = 0
        self.vHash = []
        self.vBits = []

    def deserialize(self, f):
        self.nTransactions = struct.unpack("<i", f.read(4))[0]
        self.vHash = deser_uint256_vector(f)
        vBytes = deser_string(f)
        self.vBits = []
        for i in range(len(vBytes) * 8):
            self.vBits.append(vBytes[i // 8] & (1 << (i % 8)) != 0)

    def serialize(self):
        r = b""
        r += struct.pack("<i", self.nTransactions)
        r += ser_uint256_vector(self.vHash)
        vBytesArray = bytearray([0x00] * ((len(self.vBits) + 7) // 8))
        for i in range(len(self.vBits)):
            vBytesArray[i // 8] |= self.vBits[i] << (i % 8)
        r += ser_string(bytes(vBytesArray))
        return r

    def __repr__(self):
        return "CPartialMerkleTree(nTransactions={}, vHash={}, vBits={})".format(
            self.nTransactions, repr(self.vHash), repr(self.vBits))


class CMerkleBlock:
    __slots__ = ("header", "txn")

    def __init__(self):
        self.header = CBlockHeader()
        self.txn = CPartialMerkleTree()

    def deserialize(self, f):
        self.header.deserialize(f)
        self.txn.deserialize(f)

    def serialize(self):
        r = b""
        r += self.header.serialize()
        r += self.txn.serialize()
        return r

    def __repr__(self):
        return "CMerkleBlock(header={}, txn={})".format(
            repr(self.header), repr(self.txn))


# Objects that correspond to messages on the wire

class msg_version:
    __slots__ = ("addrFrom", "addrTo", "nNonce", "nRelay", "nServices",
                 "nStartingHeight", "nTime", "nVersion", "strSubVer", "nExtraEntropy")
    msgtype = b"version"

    def __init__(self):
        self.nVersion = MY_VERSION
        self.nServices = 1
        self.nTime = int(time.time())
        self.addrTo = CAddress()
        self.addrFrom = CAddress()
        self.nNonce = random.getrandbits(64)
        self.strSubVer = MY_SUBVERSION
        self.nStartingHeight = -1
        self.nRelay = MY_RELAY
        self.nExtraEntropy = random.getrandbits(64)

    def deserialize(self, f):
        self.nVersion = struct.unpack("<i", f.read(4))[0]
        self.nServices = struct.unpack("<Q", f.read(8))[0]
        self.nTime = struct.unpack("<q", f.read(8))[0]
        self.addrTo = CAddress()
        self.addrTo.deserialize(f, with_time=False)

        self.addrFrom = CAddress()
        self.addrFrom.deserialize(f, with_time=False)
        self.nNonce = struct.unpack("<Q", f.read(8))[0]
        self.strSubVer = deser_string(f)

        self.nStartingHeight = struct.unpack("<i", f.read(4))[0]

        self.nRelay = struct.unpack("<b", f.read(1))[0]

        self.nExtraEntropy = struct.unpack("<Q", f.read(8))[0]

    def serialize(self):
        r = b""
        r += struct.pack("<i", self.nVersion)
        r += struct.pack("<Q", self.nServices)
        r += struct.pack("<q", self.nTime)
        r += self.addrTo.serialize(with_time=False)
        r += self.addrFrom.serialize(with_time=False)
        r += struct.pack("<Q", self.nNonce)
        r += ser_string(self.strSubVer)
        r += struct.pack("<i", self.nStartingHeight)
        r += struct.pack("<b", self.nRelay)
        r += struct.pack("<Q", self.nExtraEntropy)
        return r

    def __repr__(self):
        return 'msg_version(nVersion={} nServices={} nTime={} addrTo={} addrFrom={} nNonce=0x{:016X} strSubVer={} nStartingHeight={} nRelay={} nExtraEntropy={})'.format(
            self.nVersion, self.nServices, self.nTime,
            repr(self.addrTo), repr(self.addrFrom), self.nNonce,
            self.strSubVer, self.nStartingHeight, self.nRelay, self.nExtraEntropy)


class msg_verack:
    __slots__ = ()
    msgtype = b"verack"

    def __init__(self):
        pass

    def deserialize(self, f):
        pass

    def serialize(self):
        return b""

    def __repr__(self):
        return "msg_verack()"


class msg_addr:
    __slots__ = ("addrs",)
    msgtype = b"addr"

    def __init__(self):
        self.addrs = []

    def deserialize(self, f):
        self.addrs = deser_vector(f, CAddress)

    def serialize(self):
        return ser_vector(self.addrs)

    def __repr__(self):
        return "msg_addr(addrs={})".format(repr(self.addrs))


class msg_addrv2:
    __slots__ = ("addrs",)
    msgtype = b"addrv2"

    def __init__(self):
        self.addrs = []

    def deserialize(self, f):
        self.addrs = deser_vector(f, CAddress, "deserialize_v2")

    def serialize(self):
        return ser_vector(self.addrs, "serialize_v2")

    def __repr__(self):
        return "msg_addrv2(addrs={})".format(repr(self.addrs))


class msg_sendaddrv2:
    __slots__ = ()
    msgtype = b"sendaddrv2"

    def __init__(self):
        pass

    def deserialize(self, f):
        pass

    def serialize(self):
        return b""

    def __repr__(self):
        return "msg_sendaddrv2()"


class msg_inv:
    __slots__ = ("inv",)
    msgtype = b"inv"

    def __init__(self, inv=None):
        if inv is None:
            self.inv = []
        else:
            self.inv = inv

    def deserialize(self, f):
        self.inv = deser_vector(f, CInv)

    def serialize(self):
        return ser_vector(self.inv)

    def __repr__(self):
        return "msg_inv(inv={})".format(repr(self.inv))


class msg_getdata:
    __slots__ = ("inv",)
    msgtype = b"getdata"

    def __init__(self, inv=None):
        self.inv = inv if inv is not None else []

    def deserialize(self, f):
        self.inv = deser_vector(f, CInv)

    def serialize(self):
        return ser_vector(self.inv)

    def __repr__(self):
        return "msg_getdata(inv={})".format(repr(self.inv))


class msg_getblocks:
    __slots__ = ("locator", "hashstop")
    msgtype = b"getblocks"

    def __init__(self):
        self.locator = CBlockLocator()
        self.hashstop = 0

    def deserialize(self, f):
        self.locator = CBlockLocator()
        self.locator.deserialize(f)
        self.hashstop = deser_uint256(f)

    def serialize(self):
        r = b""
        r += self.locator.serialize()
        r += ser_uint256(self.hashstop)
        return r

    def __repr__(self):
        return "msg_getblocks(locator={} hashstop={:064x})".format(
            repr(self.locator), self.hashstop)


class msg_tx:
    __slots__ = ("tx",)
    msgtype = b"tx"

    def __init__(self, tx=CTransaction()):
        self.tx = tx

    def deserialize(self, f):
        self.tx.deserialize(f)

    def serialize(self):
        return self.tx.serialize()

    def __repr__(self):
        return "msg_tx(tx={})".format(repr(self.tx))


class msg_block:
    __slots__ = ("block",)
    msgtype = b"block"

    def __init__(self, block=None):
        if block is None:
            self.block = CBlock()
        else:
            self.block = block

    def deserialize(self, f):
        self.block.deserialize(f)

    def serialize(self):
        return self.block.serialize()

    def __repr__(self):
        return "msg_block(block={})".format(repr(self.block))


# for cases where a user needs tighter control over what is sent over the wire
# note that the user must supply the name of the msgtype, and the data
class msg_generic:
    __slots__ = ("msgtype", "data")

    def __init__(self, msgtype, data=None):
        self.msgtype = msgtype
        self.data = data

    def serialize(self):
        return self.data

    def __repr__(self):
        return "msg_generic()"


class msg_getaddr:
    __slots__ = ()
    msgtype = b"getaddr"

    def __init__(self):
        pass

    def deserialize(self, f):
        pass

    def serialize(self):
        return b""

    def __repr__(self):
        return "msg_getaddr()"


class msg_ping:
    __slots__ = ("nonce",)
    msgtype = b"ping"

    def __init__(self, nonce=0):
        self.nonce = nonce

    def deserialize(self, f):
        self.nonce = struct.unpack("<Q", f.read(8))[0]

    def serialize(self):
        r = b""
        r += struct.pack("<Q", self.nonce)
        return r

    def __repr__(self):
        return "msg_ping(nonce={:08x})".format(self.nonce)


class msg_pong:
    __slots__ = ("nonce",)
    msgtype = b"pong"

    def __init__(self, nonce=0):
        self.nonce = nonce

    def deserialize(self, f):
        self.nonce = struct.unpack("<Q", f.read(8))[0]

    def serialize(self):
        r = b""
        r += struct.pack("<Q", self.nonce)
        return r

    def __repr__(self):
        return "msg_pong(nonce={:08x})".format(self.nonce)


class msg_mempool:
    __slots__ = ()
    msgtype = b"mempool"

    def __init__(self):
        pass

    def deserialize(self, f):
        pass

    def serialize(self):
        return b""

    def __repr__(self):
        return "msg_mempool()"


class msg_notfound:
    __slots__ = ("vec", )
    msgtype = b"notfound"

    def __init__(self, vec=None):
        self.vec = vec or []

    def deserialize(self, f):
        self.vec = deser_vector(f, CInv)

    def serialize(self):
        return ser_vector(self.vec)

    def __repr__(self):
        return "msg_notfound(vec={})".format(repr(self.vec))


class msg_sendheaders:
    __slots__ = ()
    msgtype = b"sendheaders"

    def __init__(self):
        pass

    def deserialize(self, f):
        pass

    def serialize(self):
        return b""

    def __repr__(self):
        return "msg_sendheaders()"


# getheaders message has
# number of entries
# vector of hashes
# hash_stop (hash of last desired block header, 0 to get as many as possible)
class msg_getheaders:
    __slots__ = ("hashstop", "locator",)
    msgtype = b"getheaders"

    def __init__(self):
        self.locator = CBlockLocator()
        self.hashstop = 0

    def deserialize(self, f):
        self.locator = CBlockLocator()
        self.locator.deserialize(f)
        self.hashstop = deser_uint256(f)

    def serialize(self):
        r = b""
        r += self.locator.serialize()
        r += ser_uint256(self.hashstop)
        return r

    def __repr__(self):
        return "msg_getheaders(locator={}, stop={:064x})".format(
            repr(self.locator), self.hashstop)


# headers message has
# <count> <vector of block headers>
class msg_headers:
    __slots__ = ("headers",)
    msgtype = b"headers"

    def __init__(self, headers=None):
        self.headers = headers if headers is not None else []

    def deserialize(self, f):
        # comment in bitcoind indicates these should be deserialized as blocks
        self.headers = deser_vector(f, CBlockHeader)

    def serialize(self):
        return ser_vector([CBlockHeader(header) for header in self.headers])

    def __repr__(self):
        return "msg_headers(headers={})".format(repr(self.headers))


class msg_merkleblock:
    __slots__ = ("merkleblock",)
    msgtype = b"merkleblock"

    def __init__(self, merkleblock=None):
        if merkleblock is None:
            self.merkleblock = CMerkleBlock()
        else:
            self.merkleblock = merkleblock

    def deserialize(self, f):
        self.merkleblock.deserialize(f)

    def serialize(self):
        return self.merkleblock.serialize()

    def __repr__(self):
        return "msg_merkleblock(merkleblock={})".format(repr(self.merkleblock))


class msg_filterload:
    __slots__ = ("data", "nHashFuncs", "nTweak", "nFlags")
    msgtype = b"filterload"

    def __init__(self, data=b'00', nHashFuncs=0, nTweak=0, nFlags=0):
        self.data = data
        self.nHashFuncs = nHashFuncs
        self.nTweak = nTweak
        self.nFlags = nFlags

    def deserialize(self, f):
        self.data = deser_string(f)
        self.nHashFuncs = struct.unpack("<I", f.read(4))[0]
        self.nTweak = struct.unpack("<I", f.read(4))[0]
        self.nFlags = struct.unpack("<B", f.read(1))[0]

    def serialize(self):
        r = b""
        r += ser_string(self.data)
        r += struct.pack("<I", self.nHashFuncs)
        r += struct.pack("<I", self.nTweak)
        r += struct.pack("<B", self.nFlags)
        return r

    def __repr__(self):
        return "msg_filterload(data={}, nHashFuncs={}, nTweak={}, nFlags={})".format(
            self.data, self.nHashFuncs, self.nTweak, self.nFlags)


class msg_filteradd:
    __slots__ = ("data")
    msgtype = b"filteradd"

    def __init__(self, data):
        self.data = data

    def deserialize(self, f):
        self.data = deser_string(f)

    def serialize(self):
        r = b""
        r += ser_string(self.data)
        return r

    def __repr__(self):
        return "msg_filteradd(data={})".format(self.data)


class msg_filterclear:
    __slots__ = ()
    msgtype = b"filterclear"

    def __init__(self):
        pass

    def deserialize(self, f):
        pass

    def serialize(self):
        return b""

    def __repr__(self):
        return "msg_filterclear()"


class msg_feefilter:
    __slots__ = ("feerate",)
    msgtype = b"feefilter"

    def __init__(self, feerate=0):
        self.feerate = feerate

    def deserialize(self, f):
        self.feerate = struct.unpack("<Q", f.read(8))[0]

    def serialize(self):
        r = b""
        r += struct.pack("<Q", self.feerate)
        return r

    def __repr__(self):
        return "msg_feefilter(feerate={:08x})".format(self.feerate)


class msg_sendcmpct:
    __slots__ = ("announce", "version")
    msgtype = b"sendcmpct"

    def __init__(self, announce=False, version=1):
        self.announce = announce
        self.version = version

    def deserialize(self, f):
        self.announce = struct.unpack("<?", f.read(1))[0]
        self.version = struct.unpack("<Q", f.read(8))[0]

    def serialize(self):
        r = b""
        r += struct.pack("<?", self.announce)
        r += struct.pack("<Q", self.version)
        return r

    def __repr__(self):
        return "msg_sendcmpct(announce={}, version={})".format(
            self.announce, self.version)


class msg_cmpctblock:
    __slots__ = ("header_and_shortids",)
    msgtype = b"cmpctblock"

    def __init__(self, header_and_shortids=None):
        self.header_and_shortids = header_and_shortids

    def deserialize(self, f):
        self.header_and_shortids = P2PHeaderAndShortIDs()
        self.header_and_shortids.deserialize(f)

    def serialize(self):
        r = b""
        r += self.header_and_shortids.serialize()
        return r

    def __repr__(self):
        return "msg_cmpctblock(HeaderAndShortIDs={})".format(
            repr(self.header_and_shortids))


class msg_getblocktxn:
    __slots__ = ("block_txn_request",)
    msgtype = b"getblocktxn"

    def __init__(self):
        self.block_txn_request = None

    def deserialize(self, f):
        self.block_txn_request = BlockTransactionsRequest()
        self.block_txn_request.deserialize(f)

    def serialize(self):
        r = b""
        r += self.block_txn_request.serialize()
        return r

    def __repr__(self):
        return "msg_getblocktxn(block_txn_request={})".format(
            repr(self.block_txn_request))


class msg_blocktxn:
    __slots__ = ("block_transactions",)
    msgtype = b"blocktxn"

    def __init__(self):
        self.block_transactions = BlockTransactions()

    def deserialize(self, f):
        self.block_transactions.deserialize(f)

    def serialize(self):
        r = b""
        r += self.block_transactions.serialize()
        return r

    def __repr__(self):
        return "msg_blocktxn(block_transactions={})".format(
            repr(self.block_transactions))


class msg_getcfilters:
    __slots__ = ("filter_type", "start_height", "stop_hash")
    msgtype = b"getcfilters"

    def __init__(self, filter_type, start_height, stop_hash):
        self.filter_type = filter_type
        self.start_height = start_height
        self.stop_hash = stop_hash

    def deserialize(self, f):
        self.filter_type = struct.unpack("<B", f.read(1))[0]
        self.start_height = struct.unpack("<I", f.read(4))[0]
        self.stop_hash = deser_uint256(f)

    def serialize(self):
        r = b""
        r += struct.pack("<B", self.filter_type)
        r += struct.pack("<I", self.start_height)
        r += ser_uint256(self.stop_hash)
        return r

    def __repr__(self):
        return "msg_getcfilters(filter_type={:#x}, start_height={}, stop_hash={:x})".format(
            self.filter_type, self.start_height, self.stop_hash)


class msg_cfilter:
    __slots__ = ("filter_type", "block_hash", "filter_data")
    msgtype = b"cfilter"

    def __init__(self, filter_type=None, block_hash=None, filter_data=None):
        self.filter_type = filter_type
        self.block_hash = block_hash
        self.filter_data = filter_data

    def deserialize(self, f):
        self.filter_type = struct.unpack("<B", f.read(1))[0]
        self.block_hash = deser_uint256(f)
        self.filter_data = deser_string(f)

    def serialize(self):
        r = b""
        r += struct.pack("<B", self.filter_type)
        r += ser_uint256(self.block_hash)
        r += ser_string(self.filter_data)
        return r

    def __repr__(self):
        return "msg_cfilter(filter_type={:#x}, block_hash={:x})".format(
            self.filter_type, self.block_hash)


class msg_getcfheaders:
    __slots__ = ("filter_type", "start_height", "stop_hash")
    msgtype = b"getcfheaders"

    def __init__(self, filter_type, start_height, stop_hash):
        self.filter_type = filter_type
        self.start_height = start_height
        self.stop_hash = stop_hash

    def deserialize(self, f):
        self.filter_type = struct.unpack("<B", f.read(1))[0]
        self.start_height = struct.unpack("<I", f.read(4))[0]
        self.stop_hash = deser_uint256(f)

    def serialize(self):
        r = b""
        r += struct.pack("<B", self.filter_type)
        r += struct.pack("<I", self.start_height)
        r += ser_uint256(self.stop_hash)
        return r

    def __repr__(self):
        return "msg_getcfheaders(filter_type={:#x}, start_height={}, stop_hash={:x})".format(
            self.filter_type, self.start_height, self.stop_hash)


class msg_cfheaders:
    __slots__ = ("filter_type", "stop_hash", "prev_header", "hashes")
    msgtype = b"cfheaders"

    def __init__(self, filter_type=None, stop_hash=None,
                 prev_header=None, hashes=None):
        self.filter_type = filter_type
        self.stop_hash = stop_hash
        self.prev_header = prev_header
        self.hashes = hashes

    def deserialize(self, f):
        self.filter_type = struct.unpack("<B", f.read(1))[0]
        self.stop_hash = deser_uint256(f)
        self.prev_header = deser_uint256(f)
        self.hashes = deser_uint256_vector(f)

    def serialize(self):
        r = b""
        r += struct.pack("<B", self.filter_type)
        r += ser_uint256(self.stop_hash)
        r += ser_uint256(self.prev_header)
        r += ser_uint256_vector(self.hashes)
        return r

    def __repr__(self):
        return "msg_cfheaders(filter_type={:#x}, stop_hash={:x})".format(
            self.filter_type, self.stop_hash)


class msg_getcfcheckpt:
    __slots__ = ("filter_type", "stop_hash")
    msgtype = b"getcfcheckpt"

    def __init__(self, filter_type, stop_hash):
        self.filter_type = filter_type
        self.stop_hash = stop_hash

    def deserialize(self, f):
        self.filter_type = struct.unpack("<B", f.read(1))[0]
        self.stop_hash = deser_uint256(f)

    def serialize(self):
        r = b""
        r += struct.pack("<B", self.filter_type)
        r += ser_uint256(self.stop_hash)
        return r

    def __repr__(self):
        return "msg_getcfcheckpt(filter_type={:#x}, stop_hash={:x})".format(
            self.filter_type, self.stop_hash)


class msg_cfcheckpt:
    __slots__ = ("filter_type", "stop_hash", "headers")
    msgtype = b"cfcheckpt"

    def __init__(self, filter_type=None, stop_hash=None, headers=None):
        self.filter_type = filter_type
        self.stop_hash = stop_hash
        self.headers = headers

    def deserialize(self, f):
        self.filter_type = struct.unpack("<B", f.read(1))[0]
        self.stop_hash = deser_uint256(f)
        self.headers = deser_uint256_vector(f)

    def serialize(self):
        r = b""
        r += struct.pack("<B", self.filter_type)
        r += ser_uint256(self.stop_hash)
        r += ser_uint256_vector(self.headers)
        return r

    def __repr__(self):
        return "msg_cfcheckpt(filter_type={:#x}, stop_hash={:x})".format(
            self.filter_type, self.stop_hash)


class msg_avaproof():
    __slots__ = ("proof",)
    msgtype = b"avaproof"

    def __init__(self):
        # TODO Handle both legacy and regular proof format
        self.proof = LegacyAvalancheProof()

    def deserialize(self, f):
        self.proof.deserialize(f)

    def serialize(self):
        r = b""
        r += self.proof.serialize()
        return r

    def __repr__(self):
        return "msg_avaproof(proof={})".format(repr(self.proof))


class msg_avapoll():
    __slots__ = ("poll",)
    msgtype = b"avapoll"

    def __init__(self):
        self.poll = AvalanchePoll()

    def deserialize(self, f):
        self.poll.deserialize(f)

    def serialize(self):
        r = b""
        r += self.poll.serialize()
        return r

    def __repr__(self):
        return "msg_avapoll(poll={})".format(repr(self.poll))


class msg_avaresponse():
    __slots__ = ("response",)
    msgtype = b"avaresponse"

    def __init__(self):
        self.response = AvalancheResponse()

    def deserialize(self, f):
        self.response.deserialize(f)

    def serialize(self):
        r = b""
        r += self.response.serialize()
        return r

    def __repr__(self):
        return "msg_avaresponse(response={})".format(repr(self.response))


class msg_tcpavaresponse():
    __slots__ = ("response",)
    msgtype = b"avaresponse"

    def __init__(self):
        self.response = TCPAvalancheResponse()

    def deserialize(self, f):
        self.response.deserialize(f)

    def serialize(self):
        r = b""
        r += self.response.serialize()
        return r

    def __repr__(self):
        return "msg_tcpavaresponse(response={})".format(repr(self.response))


class msg_avahello():
    __slots__ = ("hello",)
    msgtype = b"avahello"

    def __init__(self):
        self.hello = AvalancheHello()

    def deserialize(self, f):
        self.hello.deserialize(f)

    def serialize(self):
        r = b""
        r += self.hello.serialize()
        return r

    def __repr__(self):
        return "msg_avahello(response={})".format(repr(self.hello))


class TestFrameworkMessages(unittest.TestCase):
    def test_legacy_avalanche_proof_serialization_round_trip(self):
        """Verify that a LegacyAvalancheProof object is unchanged after a
        round-trip of deserialization-serialization.
        """

        proof_hex = (
            "2a00000000000000fff053650000000021030b4c866585dd868a9d62348a9cd00"
            "8d6a312937048fff31670e7e920cfc7a74401b7fc19792583e9cb39843fc5e22a"
            "4e3648ab1cb18a70290b341ee8d4f550ae2400000000102700000000000078881"
            "4004104d0de0aaeaefad02b8bdc8a01a1b8b11c696bd3d66a2c5f10780d95b7df"
            "42645cd85228a6fb29940e858e7e55842ae2bd115d1ed7cc0e82d934e929c9764"
            "8cb0ac3052d58da74de7404e84ebe2940ed2b0fe85578d8230788d8387aeaa618"
            "274b0f2edc73679fd398f60e6315258c9ec348df7fcc09340ae1af37d009719b0"
            "665"
        )

        avaproof = FromHex(LegacyAvalancheProof(), proof_hex)
        self.assertEqual(ToHex(avaproof), proof_hex)

        self.assertEqual(
            f"{avaproof.proofid:0{64}x}",
            "cb33d7fac9092089f0d473c13befa012e6ee4d19abf9a42248f731d5e59e74a2"
        )
        self.assertEqual(avaproof.sequence, 42)
        self.assertEqual(avaproof.expiration, 1699999999)
        # The master key is extracted from the key_tests.cpp.
        # Associated privkey:
        #   hex: 12b004fff7f4b69ef8650e767f18f11ede158148b425660723b9f9a66e61f747
        #   WIF: cND2ZvtabDbJ1gucx9GWH6XT9kgTAqfb6cotPt5Q5CyxVDhid2EN
        self.assertEqual(avaproof.master, bytes.fromhex(
            "030b4c866585dd868a9d62348a9cd008d6a312937048fff31670e7e920cfc7a744"
        ))
        self.assertEqual(len(avaproof.stakes), 1)
        self.assertEqual(avaproof.stakes[0].sig, bytes.fromhex(
            "c3052d58da74de7404e84ebe2940ed2b0fe85578d8230788d8387aeaa618274b"
            "0f2edc73679fd398f60e6315258c9ec348df7fcc09340ae1af37d009719b0665"
        ))
        self.assertEqual(f"{avaproof.stakes[0].stake.utxo.hash:x}",
                         "24ae50f5d4e81e340b29708ab11cab48364e2ae2c53f8439cbe983257919fcb7"
                         )
        self.assertEqual(avaproof.stakes[0].stake.utxo.n, 0)
        self.assertEqual(avaproof.stakes[0].stake.amount, 10000)
        self.assertEqual(avaproof.stakes[0].stake.height, 672828)
        self.assertEqual(avaproof.stakes[0].stake.is_coinbase, False)
        self.assertEqual(avaproof.stakes[0].stake.pubkey, bytes.fromhex(
            "04d0de0aaeaefad02b8bdc8a01a1b8b11c696bd3d66a2c5f10780d95b7df42645"
            "cd85228a6fb29940e858e7e55842ae2bd115d1ed7cc0e82d934e929c97648cb0a"
        ))

        msg_proof = msg_avaproof()
        msg_proof.proof = avaproof
        self.assertEqual(ToHex(msg_proof), proof_hex)

    def test_avalanche_proof_serialization_round_trip(self):
        """Verify that an AvalancheProof object is unchanged after a round-trip
        of deserialization-serialization.
        """

        # Extracted from proof_tests.cpp
        proof_hex = (
            "d97587e6c882615796011ec8f9a7b1c621023beefdde700a6bc02036335b4df141"
            "c8bc67bb05a971f5ac2745fd683797dde30169a79ff23e1d58c64afad42ad81cff"
            "e53967e16beb692fc5776bb442c79c5d91de00cf21804712806594010038e168a3"
            "2102449fb5237efe8f647d32e8b64f06c22d1d40368eaca2a71ffc6a13ecc8bce6"
            "804534ca1f5e22670be3df5cbd5957d8dd83d05c8f17eae391f0e7ffdce4fb3def"
            "adb7c079473ebeccf88c1f8ce87c61e451447b89c445967335ffd1aadef4299823"
            "21023beefdde700a6bc02036335b4df141c8bc67bb05a971f5ac2745fd683797dd"
            "e3ac7b0b7865200f63052ff980b93f965f398dda04917d411dd46e3c009a5fef35"
            "661fac28779b6a22760c00004f5ddf7d9865c7fead7e4a840b947939590261640f"
        )

        avaproof = FromHex(AvalancheProof(), proof_hex)
        self.assertEqual(ToHex(avaproof), proof_hex)

        self.assertEqual(
            f"{avaproof.proofid:0{64}x}",
            "455f34eb8a00b0799630071c0728481bdb1653035b1484ac33e974aa4ae7db6d"
        )
        self.assertEqual(avaproof.sequence, 6296457553413371353)
        self.assertEqual(avaproof.expiration, -4129334692075929194)
        self.assertEqual(avaproof.master, bytes.fromhex(
            "023beefdde700a6bc02036335b4df141c8bc67bb05a971f5ac2745fd683797dde3"
        ))
        # P2PK to master pubkey
        # We can't use a CScript() here because it would cause a circular
        # import
        self.assertEqual(avaproof.payout_script, bytes.fromhex(
            "21023beefdde700a6bc02036335b4df141c8bc67bb05a971f5ac2745fd683797dde3ac"))
        self.assertEqual(avaproof.signature, b64decode(
            "ewt4ZSAPYwUv+YC5P5ZfOY3aBJF9QR3UbjwAml/vNWYfrCh3m2oidgwAAE9d332YZcf+rX5KhAuUeTlZAmFkDw=="))

        self.assertEqual(len(avaproof.stakes), 1)
        self.assertEqual(avaproof.stakes[0].sig, b64decode(
            "RTTKH14iZwvj31y9WVfY3YPQXI8X6uOR8Of/3OT7Pe+tt8B5Rz6+zPiMH4zofGHkUUR7icRFlnM1/9Gq3vQpmA=="))
        self.assertEqual(f"{avaproof.stakes[0].stake.utxo.hash:x}",
                         "915d9cc742b46b77c52f69eb6be16739e5ff1cd82ad4fa4ac6581d3ef29fa769"
                         )
        self.assertEqual(avaproof.stakes[0].stake.utxo.n, 567214302)
        self.assertEqual(avaproof.stakes[0].stake.amount, 444638638000000)
        self.assertEqual(avaproof.stakes[0].stake.height, 1370779804)
        self.assertEqual(avaproof.stakes[0].stake.is_coinbase, False)
        self.assertEqual(avaproof.stakes[0].stake.pubkey, bytes.fromhex(
            "02449fb5237efe8f647d32e8b64f06c22d1d40368eaca2a71ffc6a13ecc8bce680"
        ))

        msg_proof = msg_avaproof()
        msg_proof.proof = avaproof
        self.assertEqual(ToHex(msg_proof), proof_hex)
