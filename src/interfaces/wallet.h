// Copyright (c) 2018 The Bitcoin Core developers
// Distributed under the MIT software license, see the accompanying
// file COPYING or http://www.opensource.org/licenses/mit-license.php.

#ifndef BITCOIN_INTERFACES_WALLET_H
#define BITCOIN_INTERFACES_WALLET_H

#include <amount.h>           // For Amount
#include <interfaces/chain.h> // For ChainClient
#include <primitives/blockhash.h>
#include <primitives/transaction.h> // For CTxOut
#include <pubkey.h> // For CKeyID and CScriptID (definitions needed in CTxDestination instantiation)
#include <script/sighashtype.h>
#include <script/standard.h>           // For CTxDestination
#include <support/allocators/secure.h> // For SecureString
#include <util/message.h>
#include <util/ui_change_type.h>

#include <cstdint>
#include <functional>
#include <map>
#include <memory>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

class CChainParams;
class CCoinControl;
class CKey;
class CMutableTransaction;
class COutPoint;
class CTransaction;
class CWallet;
enum class FeeReason;
enum class OutputType;
enum class TransactionError;
enum class WalletCreationStatus;
enum isminetype : unsigned int;
struct CRecipient;
struct PartiallySignedTransaction;
struct WalletContext;
typedef uint8_t isminefilter;
struct TxId;
struct bilingual_str;

namespace interfaces {

class Handler;
struct WalletAddress;
struct WalletBalances;
struct WalletTx;
struct WalletTxOut;
struct WalletTxStatus;

using WalletOrderForm = std::vector<std::pair<std::string, std::string>>;
using WalletValueMap = std::map<std::string, std::string>;

//! Interface for accessing a wallet.
class Wallet {
public:
    virtual ~Wallet() {}

    //! Encrypt wallet.
    virtual bool encryptWallet(const SecureString &wallet_passphrase) = 0;

    //! Return whether wallet is encrypted.
    virtual bool isCrypted() = 0;

    //! Lock wallet.
    virtual bool lock() = 0;

    //! Unlock wallet.
    virtual bool unlock(const SecureString &wallet_passphrase) = 0;

    //! Return whether wallet is locked.
    virtual bool isLocked() = 0;

    //! Change wallet passphrase.
    virtual bool
    changeWalletPassphrase(const SecureString &old_wallet_passphrase,
                           const SecureString &new_wallet_passphrase) = 0;

    //! Abort a rescan.
    virtual void abortRescan() = 0;

    //! Back up wallet.
    virtual bool backupWallet(const std::string &filename) = 0;

    //! Get wallet name.
    virtual std::string getWalletName() = 0;

    //! Get chainparams.
    virtual const CChainParams &getChainParams() = 0;

    //! Get set of addresses corresponding to a given label.
    virtual std::set<CTxDestination>
    getLabelAddresses(const std::string &label) = 0;

    // Get a new address.
    virtual bool getNewDestination(const OutputType type,
                                   const std::string label,
                                   CTxDestination &dest) = 0;

    //! Get public key.
    virtual bool getPubKey(const CScript &script, const CKeyID &address,
                           CPubKey &pub_key) = 0;

    //! Sign message
    virtual SigningResult signMessage(const std::string &message,
                                      const PKHash &pkhash,
                                      std::string &str_sig) = 0;

    //! Return whether wallet has private key.
    virtual bool isSpendable(const CTxDestination &dest) = 0;

    //! Return whether wallet has watch only keys.
    virtual bool haveWatchOnly() = 0;

    //! Add or update address.
    virtual bool setAddressBook(const CTxDestination &dest,
                                const std::string &name,
                                const std::string &purpose) = 0;

    // Remove address.
    virtual bool delAddressBook(const CTxDestination &dest) = 0;

    //! Look up address in wallet, return whether exists.
    virtual bool getAddress(const CTxDestination &dest, std::string *name,
                            isminetype *is_mine, std::string *purpose) = 0;

    //! Get wallet address list.
    virtual std::vector<WalletAddress> getAddresses() = 0;

    //! Add dest data.
    virtual bool addDestData(const CTxDestination &dest, const std::string &key,
                             const std::string &value) = 0;

    //! Erase dest data.
    virtual bool eraseDestData(const CTxDestination &dest,
                               const std::string &key) = 0;

    //! Get dest values with prefix.
    virtual std::vector<std::string>
    getDestValues(const std::string &prefix) = 0;

    //! Lock coin.
    virtual void lockCoin(const COutPoint &output) = 0;

    //! Unlock coin.
    virtual void unlockCoin(const COutPoint &output) = 0;

    //! Return whether coin is locked.
    virtual bool isLockedCoin(const COutPoint &output) = 0;

    //! List locked coins.
    virtual void listLockedCoins(std::vector<COutPoint> &outputs) = 0;

    //! Create transaction.
    virtual CTransactionRef
    createTransaction(const std::vector<CRecipient> &recipients,
                      const CCoinControl &coin_control, bool sign,
                      int &change_pos, Amount &fee,
                      bilingual_str &fail_reason) = 0;

    //! Commit transaction.
    virtual void commitTransaction(CTransactionRef tx, WalletValueMap value_map,
                                   WalletOrderForm order_form) = 0;

    //! Return whether transaction can be abandoned.
    virtual bool transactionCanBeAbandoned(const TxId &txid) = 0;

    //! Abandon transaction.
    virtual bool abandonTransaction(const TxId &txid) = 0;

    //! Get a transaction.
    virtual CTransactionRef getTx(const TxId &txid) = 0;

    //! Get transaction information.
    virtual WalletTx getWalletTx(const TxId &txid) = 0;

    //! Get list of all wallet transactions.
    virtual std::vector<WalletTx> getWalletTxs() = 0;

    //! Try to get updated status for a particular transaction, if possible
    //! without blocking.
    virtual bool tryGetTxStatus(const TxId &txid, WalletTxStatus &tx_status,
                                int &num_blocks, int64_t &block_time) = 0;

    //! Get transaction details.
    virtual WalletTx getWalletTxDetails(const TxId &txid,
                                        WalletTxStatus &tx_status,
                                        WalletOrderForm &order_form,
                                        bool &in_mempool, int &num_blocks) = 0;

    //! Fill PSBT.
    virtual TransactionError fillPSBT(SigHashType sighash_type, bool sign,
                                      bool bip32derivs,
                                      PartiallySignedTransaction &psbtx,
                                      bool &complete) const = 0;

    //! Get balances.
    virtual WalletBalances getBalances() = 0;

    //! Get balances if possible without blocking.
    virtual bool tryGetBalances(WalletBalances &balances,
                                BlockHash &block_hash) = 0;

    //! Get balance.
    virtual Amount getBalance() = 0;

    //! Get available balance.
    virtual Amount getAvailableBalance(const CCoinControl &coin_control) = 0;

    //! Return whether transaction input belongs to wallet.
    virtual isminetype txinIsMine(const CTxIn &txin) = 0;

    //! Return whether transaction output belongs to wallet.
    virtual isminetype txoutIsMine(const CTxOut &txout) = 0;

    //! Return debit amount if transaction input belongs to wallet.
    virtual Amount getDebit(const CTxIn &txin, isminefilter filter) = 0;

    //! Return credit amount if transaction input belongs to wallet.
    virtual Amount getCredit(const CTxOut &txout, isminefilter filter) = 0;

    //! Return AvailableCoins + LockedCoins grouped by wallet address.
    //! (put change in one group with wallet address)
    using CoinsList = std::map<CTxDestination,
                               std::vector<std::tuple<COutPoint, WalletTxOut>>>;
    virtual CoinsList listCoins() = 0;

    //! Return wallet transaction output information.
    virtual std::vector<WalletTxOut>
    getCoins(const std::vector<COutPoint> &outputs) = 0;

    //! Get required fee.
    virtual Amount getRequiredFee(unsigned int tx_bytes) = 0;

    //! Get minimum fee.
    virtual Amount getMinimumFee(unsigned int tx_bytes,
                                 const CCoinControl &coin_control) = 0;

    // Return whether HD enabled.
    virtual bool hdEnabled() = 0;

    // Return whether the wallet is blank.
    virtual bool canGetAddresses() const = 0;

    // Return whether private keys enabled.
    virtual bool privateKeysDisabled() = 0;

    // Get default address type.
    virtual OutputType getDefaultAddressType() = 0;

    //! Get max tx fee.
    virtual Amount getDefaultMaxTxFee() = 0;

    // Remove wallet.
    virtual void remove() = 0;

    //! Return whether is a legacy wallet
    virtual bool isLegacy() = 0;

    //! Register handler for unload message.
    using UnloadFn = std::function<void()>;
    virtual std::unique_ptr<Handler> handleUnload(UnloadFn fn) = 0;

    //! Register handler for show progress messages.
    using ShowProgressFn =
        std::function<void(const std::string &title, int progress)>;
    virtual std::unique_ptr<Handler> handleShowProgress(ShowProgressFn fn) = 0;

    //! Register handler for status changed messages.
    using StatusChangedFn = std::function<void()>;
    virtual std::unique_ptr<Handler>
    handleStatusChanged(StatusChangedFn fn) = 0;

    //! Register handler for address book changed messages.
    using AddressBookChangedFn = std::function<void(
        const CTxDestination &address, const std::string &label, bool is_mine,
        const std::string &purpose, ChangeType status)>;
    virtual std::unique_ptr<Handler>
    handleAddressBookChanged(AddressBookChangedFn fn) = 0;

    //! Register handler for transaction changed messages.
    using TransactionChangedFn =
        std::function<void(const TxId &txid, ChangeType status)>;
    virtual std::unique_ptr<Handler>
    handleTransactionChanged(TransactionChangedFn fn) = 0;

    //! Register handler for watchonly changed messages.
    using WatchOnlyChangedFn = std::function<void(bool have_watch_only)>;
    virtual std::unique_ptr<Handler>
    handleWatchOnlyChanged(WatchOnlyChangedFn fn) = 0;

    //! Register handler for keypool changed messages.
    using CanGetAddressesChangedFn = std::function<void()>;
    virtual std::unique_ptr<Handler>
    handleCanGetAddressesChanged(CanGetAddressesChangedFn fn) = 0;

    //! Return pointer to internal wallet class, useful for testing.
    virtual CWallet *wallet() { return nullptr; }
};

//! Wallet chain client that in addition to having chain client methods for
//! starting up, shutting down, and registering RPCs, also has additional
//! methods (called by the GUI) to load and create wallets.
class WalletClient : public ChainClient {
public:
    //! Create new wallet.
    virtual std::unique_ptr<Wallet>
    createWallet(const std::string &name, const SecureString &passphrase,
                 uint64_t wallet_creation_flags, WalletCreationStatus &status,
                 bilingual_str &error,
                 std::vector<bilingual_str> &warnings) = 0;

    //! Load existing wallet.
    virtual std::unique_ptr<Wallet>
    loadWallet(const std::string &name, bilingual_str &error,
               std::vector<bilingual_str> &warnings) = 0;

    //! Return default wallet directory.
    virtual std::string getWalletDir() = 0;

    //! Return available wallets in wallet directory.
    virtual std::vector<std::string> listWalletDir() = 0;

    //! Return interfaces for accessing wallets (if any).
    virtual std::vector<std::unique_ptr<Wallet>> getWallets() = 0;

    //! Register handler for load wallet messages. This callback is triggered by
    //! createWallet and loadWallet above, and also triggered when wallets are
    //! loaded at startup or by RPC.
    using LoadWalletFn = std::function<void(std::unique_ptr<Wallet> wallet)>;
    virtual std::unique_ptr<Handler> handleLoadWallet(LoadWalletFn fn) = 0;
};

//! Information about one wallet address.
struct WalletAddress {
    CTxDestination dest;
    isminetype is_mine;
    std::string name;
    std::string purpose;

    WalletAddress(CTxDestination destIn, isminetype isMineIn,
                  std::string nameIn, std::string purposeIn)
        : dest(std::move(destIn)), is_mine(isMineIn), name(std::move(nameIn)),
          purpose(std::move(purposeIn)) {}
};

//! Collection of wallet balances.
struct WalletBalances {
    Amount balance = Amount::zero();
    Amount unconfirmed_balance = Amount::zero();
    Amount immature_balance = Amount::zero();
    bool have_watch_only = false;
    Amount watch_only_balance = Amount::zero();
    Amount unconfirmed_watch_only_balance = Amount::zero();
    Amount immature_watch_only_balance = Amount::zero();

    bool balanceChanged(const WalletBalances &prev) const {
        return balance != prev.balance ||
               unconfirmed_balance != prev.unconfirmed_balance ||
               immature_balance != prev.immature_balance ||
               watch_only_balance != prev.watch_only_balance ||
               unconfirmed_watch_only_balance !=
                   prev.unconfirmed_watch_only_balance ||
               immature_watch_only_balance != prev.immature_watch_only_balance;
    }
};

// Wallet transaction information.
struct WalletTx {
    CTransactionRef tx;
    std::vector<isminetype> txin_is_mine;
    std::vector<isminetype> txout_is_mine;
    std::vector<CTxDestination> txout_address;
    std::vector<isminetype> txout_address_is_mine;
    Amount credit;
    Amount debit;
    Amount change;
    int64_t time;
    std::map<std::string, std::string> value_map;
    bool is_coinbase;
};

//! Updated transaction status.
struct WalletTxStatus {
    int block_height;
    int blocks_to_maturity;
    int depth_in_main_chain;
    unsigned int time_received;
    uint32_t lock_time;
    bool is_final;
    bool is_trusted;
    bool is_abandoned;
    bool is_coinbase;
    bool is_in_main_chain;
};

//! Wallet transaction output.
struct WalletTxOut {
    CTxOut txout;
    int64_t time;
    int depth_in_main_chain = -1;
    bool is_spent = false;
};

//! Return implementation of Wallet interface. This function is defined in
//! dummywallet.cpp and throws if the wallet component is not compiled.
std::unique_ptr<Wallet> MakeWallet(const std::shared_ptr<CWallet> &wallet);

//! Return implementation of ChainClient interface for a wallet client. This
//! function will be undefined in builds where ENABLE_WALLET is false.
std::unique_ptr<WalletClient>
MakeWalletClient(Chain &chain, ArgsManager &args,
                 std::vector<std::string> wallet_filenames);

} // namespace interfaces

#endif // BITCOIN_INTERFACES_WALLET_H
