/*
 * Copyright (C) 2008-2010 Trinity <http://www.trinitycore.org/>
 * Copyright (C) 2005-2009 MaNGOS <http://getmangos.com/>
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software
 * Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA 02111-1307 USA
 */

#include "AuctionHouseBot.h"
#include "AuctionHouseBotConfig.h"
#include "AuctionHouseMgr.h"
#include "Log.h"
#include "ObjectMgr.h"
#include "WorldSession.h"
#include "GameTime.h"
#include "DatabaseEnv.h"
#include "ScriptMgr.h"
#include "Config.h"
#include "Player.h"
#include "AuctionHouseSearcher.h"
#include "Field.h"

#include <algorithm>
#include <vector>
#include <set>
#include <random>
#include <sstream>
#include <map>
#include <numeric>
#include <utility>

#include "AuctionHouseBotCommon.h"
#include "AuctionHouseSearcher.h"

using namespace std;
using CharacterDatabaseQueryHolder = SQLQueryHolder<CharacterDatabaseConnection>;

AuctionHouseSearcher* sAuctionHouseSearcher = nullptr;

AuctionHouseBot::AuctionHouseBot(uint32 account, uint32 id)
{
    _account        = account;
    _id             = id;

    _lastrun_a_sec_Sell  = time(NULL);
    _lastrun_h_sec_Sell  = time(NULL);
    _lastrun_n_sec_Sell  = time(NULL);

    _lastrun_a_sec_Buy  = time(NULL);
    _lastrun_h_sec_Buy  = time(NULL);
    _lastrun_n_sec_Buy  = time(NULL);

    // Initialize _lastCleanupTime from the database
    QueryResult result = WorldDatabase.Query("SELECT UNIX_TIMESTAMP(last_cleanup_time) FROM mod_auctionhousebot_cleanup_time WHERE id = 1");
    if (result)
    {
        Field* fields = result->Fetch();
        //_lastCleanupTime = fields[0].Get<uint32>();
        _lastCleanupTime = static_cast<time_t>(fields[0].Get<uint64>());
    }
    else
    {
        // If no record exists, insert a new one with the current time
        WorldDatabase.Execute("INSERT INTO mod_auctionhousebot_cleanup_time (id, last_cleanup_time) VALUES (1, FROM_UNIXTIME({}))", time(NULL));
        _lastCleanupTime = time(NULL);
    }

    _allianceConfig = NULL;
    _hordeConfig    = NULL;
    _neutralConfig  = NULL;
}

AuctionHouseBot::~AuctionHouseBot()
{
    // Nothing
}

uint32 AuctionHouseBot::getElement(std::set<uint32> set, int index, uint32 botId, uint32 maxDup, AuctionHouseObject* auctionHouse)
{
    std::set<uint32>::iterator it = set.begin();
    std::advance(it, index);

    if (maxDup > 0)
    {
        uint32 noStacks = 0;

        for (AuctionHouseObject::AuctionEntryMap::const_iterator itr = auctionHouse->GetAuctionsBegin(); itr != auctionHouse->GetAuctionsEnd(); ++itr)
        {
            AuctionEntry* Aentry = itr->second;

            if (Aentry->owner.GetCounter() == botId)
            {
                if (*it == Aentry->item_template)
                {
                    noStacks++;
                }
            }
        }

        if (noStacks >= maxDup)
        {
            return 0;
        }
    }

    return *it;
}

uint32 AuctionHouseBot::getStackCount(AHBConfig* config, uint32 max)
{
    uint32 maxStackSize = config->GetMaxStackSize();

    if (max == 1)
    {
        return 1;
    }

    //
    // Organize the stacks in a pseudo random way
    //

    if (config->DivisibleStacks)
    {
        uint32 ret = 0;

        if (max % 5 == 0) // 5, 10, 15, 20
        {
            ret = urand(1, 4) * 5;
        }

        if (max % 4 == 0) // 4, 8, 12, 16
        {
            ret = urand(1, 4) * 4;
        }

        if (max % 3 == 0) // 3, 6, 9, 18
        {
            ret = urand(1, 3) * 3;
        }

        if (ret > maxStackSize)
        {
            ret = maxStackSize;
        }

        return ret;
    }

    // Totally random stack sizes...
    // TODO: This is not good, we need to find a better way to organize the stacks
    return urand(1, std::min(max, maxStackSize));
}

uint32 AuctionHouseBot::getElapsedTime(uint32 timeClass)
{
    switch (timeClass)
    {
    case 2:
        return urand(1, 5) * 600;   // SHORT = In the range of one hour

    case 1:
        return urand(1, 23) * 3600; // MEDIUM = In the range of one day

    default:
        return urand(1, 3) * 86400; // LONG = More than one day but less than three
    }
}

uint32 AuctionHouseBot::getNofAuctions(AHBConfig* config, AuctionHouseObject* auctionHouse, ObjectGuid guid)
{
    // All the auctions
    if (!config->ConsiderOnlyBotAuctions)
    {
        return auctionHouse->Getcount();
    }

    // Just the one handled by the bot
    uint32 count = 0;

    for (AuctionHouseObject::AuctionEntryMap::const_iterator itr = auctionHouse->GetAuctionsBegin(); itr != auctionHouse->GetAuctionsEnd(); ++itr)
    {
        AuctionEntry* Aentry = itr->second;

        if (guid == Aentry->owner)
        {
            count++;
        }
    }

    return count;
}

uint32 AuctionHouseBot::getTotalAuctions(AHBConfig* config, AuctionHouseObject* auctionHouse)
{
    uint32 totalAuctions = 0;
    for (uint32 guid : config->GetBotGUIDs())
    {
        totalAuctions += getNofAuctions(config, auctionHouse, ObjectGuid::Create<HighGuid::Player>(guid));
    }
    return totalAuctions;
}

// =============================================================================
// This routine performs the bidding/buyout operations for the bot
// =============================================================================

void AuctionHouseBot::Buy(Player* AHBplayer, AHBConfig* config, WorldSession* session)
{
    // Check if disabled
    if (!config->AHBBuyer)
    {
        LOG_INFO("module", "AHBot [{}]: Buyer is disabled", _id);
        return;
    }

    // Retrieve items not owned by the bot and not bought/bidded on by the bot
    std::string botGUIDsStr = JoinGUIDs(config->GetBotGUIDs());
    uint32 auctionHouseID = config->GetAHID();
    LOG_INFO("module", "AHBot [{}]: Querying auction house {} for items not owned by bots", _id, auctionHouseID);
    QueryResult ahContentQueryResult = CharacterDatabase.Query("SELECT id FROM auctionhouse WHERE  houseid = {} AND itemowner NOT IN ({}) AND buyguid NOT IN ({})", auctionHouseID, botGUIDsStr, botGUIDsStr);

    if (!ahContentQueryResult || ahContentQueryResult->GetRowCount() == 0)
    {
        return;
    }

    // Fetches content of selected AH to look for possible bids
    AuctionHouseObject* auctionHouseObject = sAuctionMgr->GetAuctionsMap(config->GetAHFID());
    std::vector<uint32> auctionsGuidsToConsider;

    do
    {
        uint32 auctionGuid = ahContentQueryResult->Fetch()->Get<uint32>();
        auctionsGuidsToConsider.push_back(auctionGuid);
    } while (ahContentQueryResult->NextRow());

    if (config->DebugOutBuyer)
    {
        LOG_INFO("module", "AHBot [{}]: Found {} possible bids", _id, auctionsGuidsToConsider.size());
    }

    // If it's not possible to bid stop here
    if (auctionsGuidsToConsider.empty())
    {
        if (config->DebugOutBuyer)
        {
            LOG_INFO("module", "AHBot [{}]: no auctions to bid on has been recovered in auction house {}", _id, config->GetAHID());
        }
        return;
    }

    uint32 guid = AHBplayer->GetGUID().GetCounter();

    uint32 bidsPerInterval = 1;
    if (config == _allianceConfig)
    {
        bidsPerInterval = _allianceConfig->GetAllianceBidsPerInterval();
    }
    else if (config == _hordeConfig)
    {
        bidsPerInterval = _hordeConfig->GetHordeBidsPerInterval();
    }
    else if (config == _neutralConfig)
    {
        bidsPerInterval = _neutralConfig->GetNeutralBidsPerInterval();
    }

    // Perform the operation for a maximum amount of bids attempts configured
    if (config->TraceBuyer)
    {
        LOG_INFO("module", "AHBot [{}]: Considering {} auctions per interval to bid on.", _id, bidsPerInterval);
    }

    for (uint32 count = 1; count <= bidsPerInterval; ++count)
    {
        if (config->DebugOutBuyer)
        {
            LOG_INFO("module", "AHBot [{}]: Attempting bid {}/{}", _id, count, bidsPerInterval);
        }

        if (auctionsGuidsToConsider.empty())
        {
            if (config->DebugOutBuyer)
            {
                LOG_INFO("module", "AHBot [{}]: No more auctions to consider.", _id);
            }
            break;
        }

        // Choose a random auction from possible auctions
        uint32 randomIndex = urand(0, auctionsGuidsToConsider.size() - 1);

        std::vector<uint32>::iterator itBegin = auctionsGuidsToConsider.begin();
        //std::advance(it, randomIndex);

        uint32 auctionID = auctionsGuidsToConsider.at(randomIndex);

        AuctionEntry* auction = auctionHouseObject->GetAuction(auctionID);

        // Prevent to bid again on the same auction
        auctionsGuidsToConsider.erase(itBegin + randomIndex);

        if (!auction)
        {
            if (config->DebugOutBuyer)
            {
                LOG_ERROR("module", "AHBot [{}]: Auction id: {} Possible entry to buy/bid from AH pool is invalid, this should not happen, moving on next auction", _id, auctionID);
            }
            continue;
        }

        // Prevent from buying items from the other bots
        if (gBotsId.find(auction->owner.GetCounter()) != gBotsId.end())
        {
            LOG_INFO("module", "AHBot [{}]: Skipping auction owned by another bot", _id);
            continue;
        }

        // Get the item information
        Item* pItem = sAuctionMgr->GetAItem(auction->item_guid);
        if (!pItem)
        {
            if (config->DebugOutBuyer)
            {
                LOG_ERROR("module", "AHBot [{}]: item {} doesn't exist, perhaps bought already?", _id, auction->item_guid.ToString());
            }
            continue;
        }

        // Get the item prototype
        ItemTemplate const* prototype = sObjectMgr->GetItemTemplate(auction->item_template);

        //
        // Determine current price.
        //
        uint32 currentPrice = auction->bid ? auction->bid : auction->startbid;

         //
        // Determine maximum bid and skip auctions with too high a currentPrice.
        //

        // Get price overrides
        auto [avgPrice, minPrice] = config->GetPriceOverrideForItem(prototype->ItemId);

        uint64 maxPrice = (avgPrice + ( avgPrice - minPrice ));
        uint64 SellPriceValue = maxPrice > 0 ? maxPrice : prototype->SellPrice;
        uint64 BuyPriceValue = avgPrice > 0 ? avgPrice : prototype->BuyPrice;

        long double maximumBid = 0;

        // Check that bid has an acceptable value and take bid based on vendorprice, stacksize and quality
        if (config->UseBuyPriceForBuyer)
        {
            if (prototype->Quality <= AHB_MAX_QUALITY)
            {
                // Calculate the bid based on the quality
                // old calculation used quality multiplier from DB - this is not good and will be replaced by estimated max price based on min and avg price from price override, we still need a fallback if no proceoverride is given
                //if (currentprice < SellPriceToUse * pItem->GetCount() * config->GetBuyerPrice(prototype->Quality))

                uint64 maxPriceToUse = maxPrice > 0 ? (maxPrice * pItem->GetCount()) : (SellPriceValue * pItem->GetCount() * config->GetBuyerPrice(prototype->Quality));
                if (currentPrice < maxPriceToUse)
                {
                    maximumBid = maxPriceToUse;
                }

            }
            else
            {
                if (config->DebugOutBuyer)
                {
                    LOG_ERROR("module", "AHBot [{}]: Quality {} not Supported", _id, prototype->Quality);
                }

                continue;
            }
        }
        else
        {
            if (prototype->Quality <= AHB_MAX_QUALITY)
            {
                uint64 maxPriceToUse = avgPrice > 0 ? (avgPrice * pItem->GetCount()) : (BuyPriceValue * pItem->GetCount() * config->GetBuyerPrice(prototype->Quality));
                if (currentPrice < maxPriceToUse)
                {
                    maximumBid = maxPriceToUse;
                }
            }
            else
            {
                if (config->DebugOutBuyer)
                {
                    LOG_ERROR("module", "AHBot [{}]: Quality {} not Supported", _id, prototype->Quality);
                }

                continue;
            }
        }


        //
        // Recalculate the bid depending on the type of the item
        switch (prototype->Class)
        {
        case ITEM_CLASS_PROJECTILE:
            maximumBid = 0;
            break;
        case ITEM_CLASS_GENERIC:
            maximumBid = 0;
            break;
        case ITEM_CLASS_MONEY:
            maximumBid = 0;
            break;
        case ITEM_CLASS_PERMANENT:
            maximumBid = 0;
            break;
        default:
            break;
        }

        if (config->TraceBuyer)
        {
            LOG_INFO("module", "-------------------------------------------------");
            LOG_INFO("module", "AHBot [{}]: Info for Auction #{}:", _id, auction->Id);
            LOG_INFO("module", "AHBot [{}]: AuctionHouse: {}", _id, auction->GetHouseId());
            LOG_INFO("module", "AHBot [{}]: Owner: {}", _id, auction->owner.ToString());
            LOG_INFO("module", "AHBot [{}]: Bidder: {}", _id, auction->bidder.ToString());
            LOG_INFO("module", "AHBot [{}]: Starting Bid: {}", _id, auction->startbid);
            LOG_INFO("module", "AHBot [{}]: Current Bid: {}", _id, currentPrice);
            LOG_INFO("module", "AHBot [{}]: Buyout: {}", _id, auction->buyout);
            LOG_INFO("module", "AHBot [{}]: Deposit: {}", _id, auction->deposit);
            LOG_INFO("module", "AHBot [{}]: Expire Time: {}", _id, uint32(auction->expire_time));
            LOG_INFO("module", "AHBot [{}]: Bid Max: {}", _id, maximumBid);
            LOG_INFO("module", "AHBot [{}]: Item GUID: {}", _id, auction->item_guid.ToString());
            LOG_INFO("module", "AHBot [{}]: Item Template: {}", _id, auction->item_template);
            LOG_INFO("module", "AHBot [{}]: Item ID: {}", _id, prototype->ItemId);
            LOG_INFO("module", "AHBot [{}]: Buy Price: {}", _id, prototype->BuyPrice);
            LOG_INFO("module", "AHBot [{}]: Sell Price: {}", _id, prototype->SellPrice);
            LOG_INFO("module", "AHBot [{}]: Bonding: {}", _id, prototype->Bonding);
            LOG_INFO("module", "AHBot [{}]: Quality: {}", _id, prototype->Quality);
            LOG_INFO("module", "AHBot [{}]: Item Level: {}", _id, prototype->ItemLevel);
            LOG_INFO("module", "AHBot [{}]: Ammo Type: {}", _id, prototype->AmmoType);
            LOG_INFO("module", "-------------------------------------------------");
        }

        //  Make sure to skip the auction if maximum bid is 0.
        if (maximumBid == 0)
        {
            if (config->TraceBuyer)
            {
                LOG_INFO("module", "AHBot [{}]: Maximum bid value for item class {} is 0, skipped.", _id, prototype->Class);
            }
            continue;
        }
        if (currentPrice > maximumBid)
        {
            if (config->TraceBuyer)
            {
                LOG_INFO("module", "AHBot [{}]: Current price too high, skipped.", _id);
            }
            continue;
        }

        // Calculate our bid
        double bidRate = static_cast<double>(urand(1, 100)) / 100;
        double bidValue = currentPrice + ((maximumBid - currentPrice) * bidRate);
        uint32 bidPrice = static_cast<uint32>(bidValue);


        // Check our bid is high enough to be valid. If not, correct it to minimum.
        uint32 minimumOutbid = auction->GetAuctionOutBid();
        if ((currentPrice + minimumOutbid) > bidPrice)
        {
            bidPrice = currentPrice + minimumOutbid;
        }

        if (bidPrice > maximumBid)
        {
            if (config->TraceBuyer)
            {
                LOG_INFO("module", "AHBot [{}]: Bid was above bidMax for item={} AH={}", _id, auction->item_guid.ToString(), config->GetAHID());
            }
            bidPrice = maximumBid;
        }

        if (config->DebugOutBuyer)
        {
            LOG_INFO("module", "-------------------------------------------------");
            LOG_INFO("module", "AHBot [{}]: Bid Rate: {}", _id, bidRate);
            LOG_INFO("module", "AHBot [{}]: Bid Value: {}", _id, bidValue);
            LOG_INFO("module", "AHBot [{}]: Bid Price: {}", _id, bidPrice);
            LOG_INFO("module", "AHBot [{}]: Minimum Outbid: {}", _id, minimumOutbid);
            LOG_INFO("module", "-------------------------------------------------");
        }

        //
        // Check whether we do normal bid, or buyout
        //

        if ((bidPrice < auction->buyout) || (auction->buyout == 0))
        {
            //
            // Perform a new bid on the auction
            //

            if (auction->bidder)
            {
                if (auction->bidder != AHBplayer->GetGUID())
                {
                    // Mail to last bidder and return their money
                    //

                    auto trans = CharacterDatabase.BeginTransaction();
                    sAuctionMgr->SendAuctionOutbiddedMail(auction, bidPrice, session->GetPlayer(), trans);
                    CharacterDatabase.CommitTransaction(trans);
                }
            }

            auction->bidder = AHBplayer->GetGUID();
            auction->bid = bidPrice;
            sAuctionMgr->GetAuctionHouseSearcher()->UpdateBid(auction);

            //
            // update/save the auction into database
            //
            CharacterDatabase.Execute("UPDATE auctionhouse SET buyguid = '{}', lastbid = '{}' WHERE id = '{}'", auction->bidder.GetCounter(), auction->bid, auction->Id);

            //CharacterDatabase.Execute("UPDATE auctionhouse SET buyguid = '{}', lastbid = '{}' WHERE id = '{}'", auction->bidder.GetCounter(), auction->bid, auction->Id);
            if (config->TraceBuyer)
            {
                LOG_INFO("module", "AHBot [{}]: New bid, itemid={}, ah={}, auctionId={} item={}, start={}, current={}, buyout={}", _id, prototype->ItemId, auction->GetHouseId(), auction->Id, auction->item_template, auction->startbid, currentPrice, auction->buyout);
            }
        }
        else
        {
            //
            // Perform the buyout
            auto trans = CharacterDatabase.BeginTransaction();

            if ((auction->bidder) && (AHBplayer->GetGUID() != auction->bidder))
            {
                //
                //  Mail to last bidder and return their money
                //

                sAuctionMgr->SendAuctionOutbiddedMail(auction, auction->buyout, session->GetPlayer(), trans);
            }

            auction->bidder = AHBplayer->GetGUID();
            auction->bid = auction->buyout;

            // Send mails to buyer & seller
            sAuctionMgr->SendAuctionSuccessfulMail(auction, trans);
            sAuctionMgr->SendAuctionWonMail(auction, trans);

            // Removes any trace of the item
            //

            ScriptMgr::instance()->OnAuctionSuccessful(auctionHouseObject, auction);
            auction->DeleteFromDB(trans);
            sAuctionMgr->RemoveAItem(auction->item_guid);
            auctionHouseObject->RemoveAuction(auction);

            CharacterDatabase.CommitTransaction(trans);

            if (config->TraceBuyer)
            {
                LOG_INFO("module", "AHBot [{}]: Bought , itemid={}, ah={}, item={}, start={}, current={}, buyout={}", _id, prototype->ItemId, AuctionHouseId(auction->GetHouseId()), auction->item_template, auction->startbid, currentPrice, auction->buyout);
            }
        }
    }

    LOG_INFO("module", "AHBot [{}]: Completed buying process", _id);
}

// =============================================================================
// This routine performs the selling operations for the bot
// =============================================================================

void AuctionHouseBot::Sell(Player* AHBplayer, AHBConfig* config)
{
    // Check if disabled
    if (!config->AHBSeller)
    {
        LOG_INFO("module", "AHBot [{}]: Seller is disabled", _id);
        return;
    }

    // Retrieve the auction house situation
    AuctionHouseEntry const* ahEntry = sAuctionMgr->GetAuctionHouseEntryFromFactionTemplate(config->GetAHFID());
    if (!ahEntry)
    {
        LOG_ERROR("module", "AHBot [{}]: Could not retrieve auction house entry", _id);
        return;
    }

    AuctionHouseObject* auctionHouse = sAuctionMgr->GetAuctionsMap(config->GetAHFID());
    if (!auctionHouse)
    {
        LOG_ERROR("module", "AHBot [{}]: Could not retrieve auction house object", _id);
        return;
    }

    //
    // Check the given limits
    //
    uint32 totalAuctions = getTotalAuctions(config, auctionHouse);
    uint32 minTotalItems = config->GetMinItems();
    uint32 maxTotalItems = config->GetMaxItems();
    uint32 maxItemsToList = 0;

    if (maxTotalItems == 0  || totalAuctions >= maxTotalItems)
    {
        return;
    }

    // don't mess with the AH update let server do it.
    //auctionHouseObject->Update();

    //
    // Check if we are clear to proceed
    //

    // Divide maxItems by the number of bots to get the max items per bot
    uint32 numBots = config->GetBotGUIDs().size();
    uint32 maxAuctionsPerBot = (maxTotalItems + numBots - 1) / numBots;
    uint32 minAuctionsPerBot = (minTotalItems + numBots - 1) / numBots;

    bool   aboveMin = false;
    bool   aboveMax = false;
    uint32 nbOfAuctions = getNofAuctions(config, auctionHouse, AHBplayer->GetGUID());

    uint32 nbItemsToSellThisCycle = 0;

    if (nbOfAuctions >= minTotalItems)
    {
        if (config->DebugOutSeller)
        {
            LOG_TRACE("module", "AHBot [{}]: Auctions above minimum", _id);
        }
    }

    if (nbOfAuctions >= maxAuctionsPerBot)
    {
        aboveMax = true;
        if (config->DebugOutSeller)
        {
            std::string guidStr = AHBplayer->GetGUID().ToString();
            LOG_ERROR("module", "AHBot [{}]: Auctions above maximum for bot {}", _id, guidStr);
        }
        return;
    }

     if (totalAuctions < minTotalItems)
     {
         // Add new auctions quickly until totalAuctions reach minItems
         maxItemsToList = minAuctionsPerBot - nbOfAuctions;

         // Ensure maxItemsToList is less than or equal to minAuctionsPerBot
         if (maxItemsToList > minAuctionsPerBot)
         {
             maxItemsToList = minAuctionsPerBot;
         }

         nbItemsToSellThisCycle = urand(maxItemsToList, minAuctionsPerBot);
    }
    else
    {
        // Gradually increase the number of auctions with ItemsPerCycle
        maxItemsToList = maxAuctionsPerBot - nbOfAuctions;
        nbItemsToSellThisCycle = config->ItemsPerCycle;

        if (nbItemsToSellThisCycle > maxItemsToList)
        {
            nbItemsToSellThisCycle = maxItemsToList;
        }
    }

    // Log the number of items to list
    if (config->DebugOutSeller)
    {
        LOG_INFO("module", "AHBot [{}]: Trying to list {} items", _id, nbItemsToSellThisCycle);
    }

    // Use the max stack size configuration value
    uint32 maxStackSize = config->GetMaxStackSize();

    // Retrieve the configuration for this run
    //

    uint32 maxGreyTG   = config->GetMaximum(AHB_GREY_TG);
    uint32 maxWhiteTG  = config->GetMaximum(AHB_WHITE_TG);
    uint32 maxGreenTG  = config->GetMaximum(AHB_GREEN_TG);
    uint32 maxBlueTG   = config->GetMaximum(AHB_BLUE_TG);
    uint32 maxPurpleTG = config->GetMaximum(AHB_PURPLE_TG);
    uint32 maxOrangeTG = config->GetMaximum(AHB_ORANGE_TG);
    uint32 maxYellowTG = config->GetMaximum(AHB_YELLOW_TG);

    uint32 maxGreyI    = config->GetMaximum(AHB_GREY_I);
    uint32 maxWhiteI   = config->GetMaximum(AHB_WHITE_I);
    uint32 maxGreenI   = config->GetMaximum(AHB_GREEN_I);
    uint32 maxBlueI    = config->GetMaximum(AHB_BLUE_I);
    uint32 maxPurpleI  = config->GetMaximum(AHB_PURPLE_I);
    uint32 maxOrangeI  = config->GetMaximum(AHB_ORANGE_I);
    uint32 maxYellowI  = config->GetMaximum(AHB_YELLOW_I);

    uint32 currentGreyTG    = config->GetItemCounts(AHB_GREY_TG);
    uint32 currentWhiteTG   = config->GetItemCounts(AHB_WHITE_TG);
    uint32 currentGreenTG   = config->GetItemCounts(AHB_GREEN_TG);
    uint32 currentBlueTG    = config->GetItemCounts(AHB_BLUE_TG);
    uint32 currentPurpleTG  = config->GetItemCounts(AHB_PURPLE_TG);
    uint32 currentOrangeTG  = config->GetItemCounts(AHB_ORANGE_TG);
    uint32 currentYellowTG  = config->GetItemCounts(AHB_YELLOW_TG);

    uint32 currentGreyItems     = config->GetItemCounts(AHB_GREY_I);
    uint32 currentWhiteItems    = config->GetItemCounts(AHB_WHITE_I);
    uint32 currentGreenItems    = config->GetItemCounts(AHB_GREEN_I);
    uint32 currentBlueItems     = config->GetItemCounts(AHB_BLUE_I);
    uint32 currentPurpleItems   = config->GetItemCounts(AHB_PURPLE_I);
    uint32 currentOrangeItems   = config->GetItemCounts(AHB_ORANGE_I);
    uint32 currentYellowItems   = config->GetItemCounts(AHB_YELLOW_I);

    // Initialize item counts
    std::vector<uint32> itemCounts(14, 0);

    // Get prioritized item IDs
    std::vector<uint32> itemsToSell = GetItemsToSell(config, AHBplayer->GetGUID());

    // Loop variables
    uint32 nbSold    = 0; // Tracing counter
    uint32 binEmpty  = 0; // Tracing counter
    uint32 noNeed    = 0; // Tracing counter
    uint32 tooMany   = 0; // Tracing counter
    uint32 loopBrk   = 0; // Tracing counter
    uint32 err       = 0; // Tracing counter

    for (uint32 cnt = 0; cnt < nbItemsToSellThisCycle && cnt < itemsToSell.size(); ++cnt)
    {
        uint32 itemTypeSelectedToSell = 0;

        uint32 itemID = itemsToSell[cnt];

        // Update Auctions count for current Bot
        nbOfAuctions = getNofAuctions(config, auctionHouse, AHBplayer->GetGUID());

        if (nbOfAuctions >= maxAuctionsPerBot)
        {
            LOG_INFO("module", "AHBot [{}]: Reached max auctions per bot: {}", _id, maxAuctionsPerBot);
            break;
        }

        uint32 loopbreaker = 0;

        //
        // Select, in rarity order, a new random item
        //

        while (itemID == 0 && loopbreaker <= AUCTION_HOUSE_BOT_LOOP_BREAKER)
        {
            loopbreaker++;

            // Poor

            if ((config->GreyItemsBin.size() > 0) && (currentGreyItems < maxGreyI))
            {
                itemTypeSelectedToSell = AHB_GREY_I;
                itemID = getElement(config->GreyItemsBin, urand(0, config->GreyItemsBin.size() - 1), _id, config->DuplicatesCount, auctionHouse);
            }

            if (itemID == 0 && (config->GreyTradeGoodsBin.size() > 0) && (currentGreyTG < maxGreyTG))
            {
                itemTypeSelectedToSell = AHB_GREY_TG;
                itemID = getElement(config->GreyTradeGoodsBin, urand(0, config->GreyTradeGoodsBin.size() - 1), _id, config->DuplicatesCount, auctionHouse);
            }

            // Normal

            if (itemID == 0 && (config->WhiteItemsBin.size() > 0) && (currentWhiteItems < maxWhiteI))
            {
                itemTypeSelectedToSell = AHB_WHITE_I;
                itemID = getElement(config->WhiteItemsBin, urand(0, config->WhiteItemsBin.size() - 1), _id, config->DuplicatesCount, auctionHouse);
            }

            if (itemID == 0 && (config->WhiteTradeGoodsBin.size() > 0) && (currentWhiteTG < maxWhiteTG))
            {
                itemTypeSelectedToSell = AHB_WHITE_TG;
                itemID = getElement(config->WhiteTradeGoodsBin, urand(0, config->WhiteTradeGoodsBin.size() - 1), _id, config->DuplicatesCount, auctionHouse);
            }

            // Uncommon

            if (itemID == 0 && (config->GreenItemsBin.size() > 0) && (currentGreenItems < maxGreenI))
            {
                itemTypeSelectedToSell = AHB_GREEN_I;
                itemID = getElement(config->GreenItemsBin, urand(0, config->GreenItemsBin.size() - 1), _id, config->DuplicatesCount, auctionHouse);
            }

            if (itemID == 0 && (config->GreenTradeGoodsBin.size() > 0) && (currentGreenTG < maxGreenTG))
            {
                itemTypeSelectedToSell = AHB_GREEN_TG;
                itemID = getElement(config->GreenTradeGoodsBin, urand(0, config->GreenTradeGoodsBin.size() - 1), _id, config->DuplicatesCount, auctionHouse);
            }

            // Rare

            if (itemID == 0 && (config->BlueItemsBin.size() > 0) && (currentBlueItems < maxBlueI))
            {
                itemTypeSelectedToSell = AHB_BLUE_I;
                itemID = getElement(config->BlueItemsBin, urand(0, config->BlueItemsBin.size() - 1), _id, config->DuplicatesCount, auctionHouse);
            }

            if (itemID == 0 && (config->BlueTradeGoodsBin.size() > 0) && (currentBlueTG < maxBlueTG))
            {
                itemTypeSelectedToSell = AHB_BLUE_TG;
                itemID = getElement(config->BlueTradeGoodsBin, urand(0, config->BlueTradeGoodsBin.size() - 1), _id, config->DuplicatesCount, auctionHouse);
            }

            // Epic

            if (itemID == 0 && (config->PurpleItemsBin.size() > 0) && (currentPurpleItems < maxPurpleI))
            {
                itemTypeSelectedToSell = AHB_PURPLE_I;
                itemID = getElement(config->PurpleItemsBin, urand(0, config->PurpleItemsBin.size() - 1), _id, config->DuplicatesCount, auctionHouse);
            }

            if (itemID == 0 && (config->PurpleTradeGoodsBin.size() > 0) && (currentPurpleTG < maxPurpleTG))
            {
                itemTypeSelectedToSell = AHB_PURPLE_TG;
                itemID = getElement(config->PurpleTradeGoodsBin, urand(0, config->PurpleTradeGoodsBin.size() - 1), _id, config->DuplicatesCount, auctionHouse);
            }

            // Legendary

            if (itemID == 0 && (config->OrangeItemsBin.size() > 0) && (currentOrangeItems < maxOrangeI))
            {
                itemTypeSelectedToSell = AHB_ORANGE_I;
                itemID = getElement(config->OrangeItemsBin, urand(0, config->OrangeItemsBin.size() - 1), _id, config->DuplicatesCount, auctionHouse);
            }

            if (itemID == 0 && (config->OrangeTradeGoodsBin.size() > 0) && (currentOrangeTG < maxOrangeTG))
            {
                itemTypeSelectedToSell = AHB_ORANGE_TG;
                itemID = getElement(config->OrangeTradeGoodsBin, urand(0, config->OrangeTradeGoodsBin.size() - 1), _id, config->DuplicatesCount, auctionHouse);
            }

            // Artifact

            if (itemID == 0 && (config->YellowItemsBin.size() > 0) && (currentYellowItems < maxYellowI))
            {
                itemTypeSelectedToSell = AHB_YELLOW_I;
                itemID = getElement(config->YellowItemsBin, urand(0, config->YellowItemsBin.size() - 1), _id, config->DuplicatesCount, auctionHouse);
            }

            if (itemID == 0 && (config->YellowTradeGoodsBin.size() > 0) && (currentYellowTG < maxYellowTG))
            {
                itemTypeSelectedToSell = AHB_YELLOW_TG;
                itemID = getElement(config->YellowTradeGoodsBin, urand(0, config->YellowTradeGoodsBin.size() - 1), _id, config->DuplicatesCount, auctionHouse);
            }

            if (itemID == 0)
            {
                if (config->DebugOutSeller)
                {
                    LOG_ERROR("module", "AHBot [{}]: No item could be selected from the bins", _id);
                }

                break;
            }
        }

        if (itemID == 0)
        {
            loopBrk++;
            return;
        }

        // Retrieve information about the selected item
        ItemTemplate const* prototype = sObjectMgr->GetItemTemplate(itemID);

        if (prototype == NULL)
        {
            err++;

            if (config->DebugOutSeller)
            {
                LOG_ERROR("module", "AHBot [{}]: could not get prototype of item {}", _id, itemID);
            }

            return;
        }

        Item* item = Item::CreateItem(itemID, 1, AHBplayer);

        if (item == NULL)
        {
            err++;

            if (config->DebugOutSeller)
            {
                LOG_ERROR("module", "AHBot [{}]: could not create item from prototype {}", _id, itemID);
            }

            return;
        }

        // Start interacting with the item by adding a random property
        item->AddToUpdateQueueOf(AHBplayer);

        uint32 randomPropertyId = Item::GenerateItemRandomPropertyId(itemID);

        if (randomPropertyId != 0)
        {
            item->SetItemRandomProperties(randomPropertyId);
        }

        if (prototype->Quality > AHB_MAX_QUALITY)
        {
            err++;

            if (config->DebugOutSeller)
            {
                LOG_ERROR("module", "AHBot [{}]: Quality {} TOO HIGH for item {}", _id, prototype->Quality, itemID);
            }

            item->RemoveFromUpdateQueueOf(AHBplayer);
            return;
        }

        // Determine the price
        uint64 buyoutPrice = 0;
        uint64 bidPrice    = 0;
        uint32 stackCount  = 1;
        uint64 baseBuyoutPrice = 0;
        uint64 baseBidPrice = 0;

        auto it = config->itemPriceOverrides.find(itemID);
        if (it != config->itemPriceOverrides.end())
        {
            // Base prices are loaded from from the price override table in the database
            baseBuyoutPrice = std::get<0>(it->second);
            baseBidPrice = std::get<1>(it->second);
        }
        else
        {
            // No price override was found, fall back to fixed default prices
            if (config->SellAtMarketPrice)
            {
                baseBuyoutPrice = config->GetItemPrice(itemID);
            }

            if (baseBuyoutPrice == 0)
            {
                if (config->UseBuyPriceForSeller)
                {
                    baseBuyoutPrice = prototype->BuyPrice;
                }
                else
                {
                    baseBuyoutPrice = prototype->SellPrice;
                }
            }

            baseBuyoutPrice = baseBuyoutPrice * urand(config->GetMinPrice(prototype->Quality), config->GetMaxPrice(prototype->Quality)) / 100;
            baseBidPrice    = baseBuyoutPrice * urand(config->GetMinBidPrice(prototype->Quality), config->GetMaxBidPrice(prototype->Quality)) / 100;
        }

        // Adjust prices based on moving average prices
        AdjustPrices(itemID, baseBuyoutPrice, baseBidPrice, config);

        // Introduce randomness around the baseline values with a range of -10% to +10%
        int32 buyoutDeviation = urand(0, 20) - 10;
        int32 bidDeviation = urand(0, 20) - 10;

        // Adjust the baseline values with random deviation
        buyoutPrice = baseBuyoutPrice * (1 + buyoutDeviation / 100.0);
        bidPrice = baseBidPrice * (1 + bidDeviation / 100.0);
        //LOG_INFO("module", "AHBot [{}]: FINAL buyout price: {}", _id, buyoutPrice);
        //LOG_INFO("module", "AHBot [{}]: FINAL bid price: {}", _id, bidPrice);

        // TODO: Implement a more sophisticated pricing strategy
        // For now, we just use the baseline prices with a random deviation
        // Ideas: - Use a moving average of the last sales prices
        //        - Use a moving average of the last bids
        //        - Use a moving average of the last buyouts
        // Make sure bidPrice is never higher than buyoutPrice - we could also think about making buyout higher than bid instead of lowering bid.
        if (bidPrice > buyoutPrice)
        {
            bidPrice = buyoutPrice;
            // increase buyout price by a random amount up to 5%
            buyoutPrice = buyoutPrice * urand(101, 105) / 100;

        }

        // Determine the stack size
        if (config->GetMaxStack(prototype->Quality) > 1 && item->GetMaxStackCount() > 1)
        {
            stackCount = minValue(getStackCount(config, item->GetMaxStackCount()), config->GetMaxStack(prototype->Quality));
        }
        else if (config->GetMaxStack(prototype->Quality) == 0 && item->GetMaxStackCount() > 1)
        {
            stackCount = getStackCount(config, item->GetMaxStackCount());
        }
        else
        {
            stackCount = 1;
        }

        item->SetCount(stackCount);

        // Determine the auction time
        //

        uint32 elapsingTime = getElapsedTime(config->ElapsingTimeClass);

        // Determine the deposit
        //

        uint32 deposit = sAuctionMgr->GetAuctionDeposit(ahEntry, elapsingTime, item, stackCount);

        // Perform the auction
        auto trans = CharacterDatabase.BeginTransaction();

        AuctionEntry* auctionEntry      = new AuctionEntry();
        auctionEntry->Id                = sObjectMgr->GenerateAuctionID();
        auctionEntry->houseId           = AuctionHouseId(config->GetAHID());
        auctionEntry->item_guid         = item->GetGUID();
        auctionEntry->item_template     = item->GetEntry();
        auctionEntry->itemCount         = item->GetCount();
        auctionEntry->owner             = AHBplayer->GetGUID();
        auctionEntry->startbid          = bidPrice * stackCount;
        auctionEntry->buyout            = buyoutPrice * stackCount;
        auctionEntry->bid               = 0;
        auctionEntry->deposit           = deposit;
        auctionEntry->expire_time       = (time_t)elapsingTime + time(NULL);
        auctionEntry->auctionHouseEntry = ahEntry;

        item->SaveToDB(trans);
        item->RemoveFromUpdateQueueOf(AHBplayer);
        sAuctionMgr->AddAItem(item);
        auctionHouse->AddAuction(auctionEntry);
        auctionEntry->SaveToDB(trans);

        CharacterDatabase.CommitTransaction(trans);

        // Increments the number of items presents in the auction
        // todo: reread config for actual values, maybe an array to not rely on local count that could potentially be mismatched from config.
        // config is updated from callback received after auctionHouseObject->AddAuction(auctionEntry);
        // or maybe reparse the server actual value each update cycle, would need profiling.
        switch (itemTypeSelectedToSell)
        {
        case AHB_GREY_I:
            ++currentGreyItems;
            break;

        case AHB_WHITE_I:
            ++currentWhiteItems;
            break;

        case AHB_GREEN_I:
            ++currentGreenItems;
            break;

        case AHB_BLUE_I:
            ++currentBlueItems;
            break;

        case AHB_PURPLE_I:
            ++currentPurpleItems;
            break;

        case AHB_ORANGE_I:
            ++currentOrangeItems;
            break;

        case AHB_YELLOW_I:
            ++currentYellowItems;
            break;

        case AHB_GREY_TG:
            ++currentGreyTG;
            break;

        case AHB_WHITE_TG:
            ++currentWhiteTG;
            break;

        case AHB_GREEN_TG:
            ++currentGreenTG;
            break;

        case AHB_BLUE_TG:
            ++currentBlueTG;
            break;

        case AHB_PURPLE_TG:
            ++currentPurpleTG;
            break;

        case AHB_ORANGE_TG:
            ++currentOrangeTG;
            break;

        case AHB_YELLOW_TG:
            ++currentYellowTG;
            break;

        default:
            break;
        }

        nbSold++;

        // Log the successful listing of an item
        if (config->TraceSeller)
        {
            LOG_INFO("module", "AHBot [{}]: AH: {} - Successfully listed item {} with stack count {}, cnt={}", _id, config->GetAHID(), itemID, stackCount, cnt);
        }

    }

    if (config->TraceSeller)
    {
        LOG_INFO("module", "AHBot [{}]: auctionhouse {}, req={}, sold={}, aboveMin={}, aboveMax={}, loopBrk={}, noNeed={}, tooMany={}, binEmpty={}, err={}", _id, config->GetAHID(), nbItemsToSellThisCycle, nbSold, aboveMin, aboveMax, loopBrk, noNeed, tooMany, binEmpty, err);
    }

}

// =============================================================================
// Get Prioritized ItemIDs
// =============================================================================

std::vector<uint32> AuctionHouseBot::GetItemsToSell(AHBConfig* config, ObjectGuid botGuid)
{
    //std::vector<uint32> prioritizedItemIDs;
    std::vector<uint32> allItemIDs;
    std::vector<uint32> tempItemIDs;

    // Log the sizes of all bins
    if (config->TraceSeller)
    {
        LOG_INFO("module", "AHBot [{}]: GreyItemsBin.size: {}", _id, config->GreyItemsBin.size());
        LOG_INFO("module", "AHBot [{}]: WhiteItemsBin.size: {}", _id, config->WhiteItemsBin.size());
        LOG_INFO("module", "AHBot [{}]: GreenItemsBin.size: {}", _id, config->GreenItemsBin.size());
        LOG_INFO("module", "AHBot [{}]: BlueItemsBin.size: {}", _id, config->BlueItemsBin.size());
        LOG_INFO("module", "AHBot [{}]: PurpleItemsBin.size: {}", _id, config->PurpleItemsBin.size());
        LOG_INFO("module", "AHBot [{}]: OrangeItemsBin.size: {}", _id, config->OrangeItemsBin.size());
        LOG_INFO("module", "AHBot [{}]: YellowItemsBin.size: {}", _id, config->YellowItemsBin.size());
    }

    // Helper function to add items to the list
    auto addItems = [&](const std::vector<uint32>& itemsBin, bool checkAuctionHouse) {
        for (auto const& itemID : itemsBin)
        {
            if (!checkAuctionHouse || !IsItemInAuctionHouse(itemID, config->GetAHID()))
            {
                tempItemIDs.push_back(itemID);
            }
        }
    };

     // Convert sets to vectors
    std::vector<uint32> greyItemsBin(config->GreyItemsBin.begin(), config->GreyItemsBin.end());
    std::vector<uint32> whiteItemsBin(config->WhiteItemsBin.begin(), config->WhiteItemsBin.end());
    std::vector<uint32> greenItemsBin(config->GreenItemsBin.begin(), config->GreenItemsBin.end());
    std::vector<uint32> blueItemsBin(config->BlueItemsBin.begin(), config->BlueItemsBin.end());
    std::vector<uint32> purpleItemsBin(config->PurpleItemsBin.begin(), config->PurpleItemsBin.end());
    std::vector<uint32> orangeItemsBin(config->OrangeItemsBin.begin(), config->OrangeItemsBin.end());
    std::vector<uint32> yellowItemsBin(config->YellowItemsBin.begin(), config->YellowItemsBin.end());


    // 1. Items with price overrides that are not listed by the bot yet (randomized order)
    std::vector<uint32> itemsWithOverridesNotListed;
    for (const auto& [itemID, _] : config->itemPriceOverrides)
    {
        if (!IsItemInAuctionHouse(itemID, config->GetAHID()))
        {
            itemsWithOverridesNotListed.push_back(itemID);
        }
    }
    std::shuffle(itemsWithOverridesNotListed.begin(), itemsWithOverridesNotListed.end(), std::mt19937(std::random_device()()));
    allItemIDs.insert(allItemIDs.end(), itemsWithOverridesNotListed.begin(), itemsWithOverridesNotListed.end());

    // 2. Items for which price overrides do exist (randomized order)
    std::vector<uint32> itemsWithOverrides;
    for (const auto& [itemID, _] : config->itemPriceOverrides)
    {
        itemsWithOverrides.push_back(itemID);
    }
    std::shuffle(itemsWithOverrides.begin(), itemsWithOverrides.end(), std::mt19937(std::random_device()()));
    allItemIDs.insert(allItemIDs.end(), itemsWithOverrides.begin(), itemsWithOverrides.end());

    // 3. Items without overrides that are not in the auction house (randomized order)
    addItems(greyItemsBin, true);
    addItems(whiteItemsBin, true);
    addItems(greenItemsBin, true);
    addItems(blueItemsBin, true);
    addItems(purpleItemsBin, true);
    addItems(orangeItemsBin, true);
    addItems(yellowItemsBin, true);

    // Randomize the collected items
    std::shuffle(tempItemIDs.begin(), tempItemIDs.end(), std::mt19937(std::random_device()()));
    allItemIDs.insert(allItemIDs.end(), tempItemIDs.begin(), tempItemIDs.end());

    // 4. Random items without price overrides (randomized order)
    tempItemIDs.clear();
    addItems(greyItemsBin, false);
    addItems(whiteItemsBin, false);
    addItems(greenItemsBin, false);
    addItems(blueItemsBin, false);
    addItems(purpleItemsBin, false);
    addItems(orangeItemsBin, false);
    addItems(yellowItemsBin, false);

    // Randomize the collected items
    std::shuffle(tempItemIDs.begin(), tempItemIDs.end(), std::mt19937(std::random_device()()));
    allItemIDs.insert(allItemIDs.end(), tempItemIDs.begin(), tempItemIDs.end());

    // Log the number of items to sell
    if(config->TraceSeller)
    {
        LOG_INFO("module", "AHBot [{}]: GetItemsToSell returning {} items", _id, allItemIDs.size());
    }

    return allItemIDs;
}

// =============================================================================
// Find out if item is listed by current bot
// =============================================================================

bool AuctionHouseBot::IsItemListedByBot(uint32 itemID, uint32 ahID, ObjectGuid botGuid)
{
    AuctionHouseObject* auctionHouse = sAuctionMgr->GetAuctionsMap(ahID);
    if (!auctionHouse)
    {
        return false;
    }

    const std::map<uint32, AuctionEntry*>& auctions = auctionHouse->GetAuctions();
    for (const auto& auction : auctions)
    {
        if (auction.second->item_template == itemID && auction.second->owner == botGuid)
        {
            return true;
        }
    }
    return false;
}

// =============================================================================
// Find out if item is listed in the aution house
// =============================================================================

bool AuctionHouseBot::IsItemInAuctionHouse(uint32 itemID, uint32 ahID)
{
    AuctionHouseObject* auctionHouse = sAuctionMgr->GetAuctionsMap(ahID);
    if (!auctionHouse)
    {
        return false;
    }

    const std::map<uint32, AuctionEntry*>& auctions = auctionHouse->GetAuctions();
    for (const auto& auction : auctions)
    {
        if (auction.second->item_template == itemID)
        {
            return true;
        }
    }
    return false;
}

// =============================================================================
// GetAllItemIDs that are not disabled and can be listed in the auctionhouse
// =============================================================================

std::vector<uint32> AuctionHouseBot::GetAllItemIDs(uint32 ahID)
{
    std::vector<uint32> allItemIDs;
    std::set<uint32> disabledItems;

    // Retrieve the list of disabled items from the database
    QueryResult result = WorldDatabase.Query("SELECT item FROM mod_auctionhousebot_disabled_items");

    if (result)
    {
        do
        {
            Field* fields = result->Fetch();
            disabledItems.insert(fields[0].Get<uint32>());
        } while (result->NextRow());
    }

    // Retrieve all item templates
    ItemTemplateContainer const* its = sObjectMgr->GetItemTemplateStore();

    for (ItemTemplateContainer::const_iterator itr = its->begin(); itr != its->end(); ++itr)
    {
        uint32 itemID = itr->second.ItemId;

        // Check if the item is not in the disabled items list
        if (disabledItems.find(itemID) == disabledItems.end() && !IsItemInAuctionHouse(itemID, ahID))
        {
            allItemIDs.push_back(itemID);
        }
    }

    return allItemIDs;
}

// =============================================================================
// Perform an update cycle
// =============================================================================

void AuctionHouseBot::Update()
{
    time_t currentTime = time(NULL);

    // Perform cleanup every 24 hours
    if (difftime(currentTime, _lastCleanupTime) >= 24 * 60 * 60)
    {
        CleanupOldAuctionHistory();
        _lastCleanupTime = currentTime;
    }

    // If no configuration is associated, then stop here
    if (!_allianceConfig && !_hordeConfig && !_neutralConfig)
    {
        return;
    }

    // Prepare for operation
    // TODO: Use account name from defined account from configuration
    std::string accountName = "AuctionHouseBot" + std::to_string(_account);

    WorldSession _session(_account, std::move(accountName), nullptr, SEC_PLAYER, sWorld->getIntConfig(CONFIG_EXPANSION), 0, LOCALE_enUS, 0, false, false, 0);

    Player _AHBplayer(&_session);
    _AHBplayer.Initialize(_id);

    ObjectAccessor::AddObject(&_AHBplayer);

    //
    // Perform update for the factions markets
    //

    // Perform update for the factions markets
    if (!sWorld->getBoolConfig(CONFIG_ALLOW_TWO_SIDE_INTERACTION_AUCTION))
    {
        // Alliance
        if (_allianceConfig)
        {
            if ((currentTime - _lastrun_a_sec_Sell) >= (_allianceConfig->GetAllianceSellingInterval() * MINUTE))
            {
                Sell(&_AHBplayer, _allianceConfig);
                _lastrun_a_sec_Sell = currentTime;
            }

            if ((currentTime - _lastrun_a_sec_Buy) >= (_allianceConfig->GetAllianceBiddingInterval() * MINUTE) && (_allianceConfig->GetAllianceBidsPerInterval() > 0))
            {
                if (_allianceConfig->TraceBuyer)
                {
                    LOG_INFO("module", "AHBot [{}]: Begin Buy for Alliance...", _id);
                }

                Buy(&_AHBplayer, _allianceConfig, &_session);
                _lastrun_a_sec_Buy = currentTime;
            }
        }

        // Horde
        if (_hordeConfig)
        {
            if ((currentTime - _lastrun_h_sec_Sell) >= (_hordeConfig->GetHordeSellingInterval() * MINUTE))
            {
                Sell(&_AHBplayer, _hordeConfig);
                _lastrun_h_sec_Sell = currentTime;
            }

            if ((currentTime - _lastrun_h_sec_Buy) >= (_hordeConfig->GetHordeBiddingInterval() * MINUTE) && (_hordeConfig->GetHordeBidsPerInterval() > 0))
            {
                if (_hordeConfig->TraceBuyer)
                {
                    LOG_INFO("module", "AHBot [{}]: Begin Buy for Horde...", _id);
                }
                Buy(&_AHBplayer, _hordeConfig, &_session);
                _lastrun_h_sec_Buy = currentTime;
            }
        }

        // Neutral
        if (_neutralConfig)
        {
            if ((currentTime - _lastrun_n_sec_Sell) >= (_neutralConfig->GetNeutralSellingInterval() * MINUTE))
            {
                Sell(&_AHBplayer, _neutralConfig);
                _lastrun_n_sec_Sell = currentTime;
            }

            if ((currentTime - _lastrun_n_sec_Buy) >= (_neutralConfig->GetNeutralBiddingInterval() * MINUTE) && (_neutralConfig->GetNeutralBidsPerInterval() > 0))
            {
                Buy(&_AHBplayer, _neutralConfig, &_session);
                _lastrun_n_sec_Buy = currentTime;
            }
        }
    }

    ObjectAccessor::RemoveObject(&_AHBplayer);
}

// =============================================================================
// Execute commands coming from the console
// =============================================================================

void AuctionHouseBot::Commands(AHBotCommand command, uint32 ahMapID, uint32 col, char* args)
{
    //
    // Retrieve the auction house configuration
    //

    AHBConfig *config = NULL;

    switch (ahMapID)
    {
    case 2:
        config = _allianceConfig;
        break;
    case 6:
        config = _hordeConfig;
        break;
    default:
        config = _neutralConfig;
        break;
    }

    //
    // Retrive the item quality
    //

    std::string color;

    switch (col)
    {
    case AHB_GREY:
        color = "grey";
        break;
    case AHB_WHITE:
        color = "white";
        break;
    case AHB_GREEN:
        color = "green";
        break;
    case AHB_BLUE:
        color = "blue";
        break;
    case AHB_PURPLE:
        color = "purple";
        break;
    case AHB_ORANGE:
        color = "orange";
        break;
    case AHB_YELLOW:
        color = "yellow";
        break;
    default:
        break;
    }

    //
    // Perform the command
    //

    switch (command)
    {
    case AHBotCommand::buyer:
    {
        char* param1 = strtok(args, " ");
        uint32 state = (uint32)strtoul(param1, NULL, 0);

        if (state == 0)
        {
            _allianceConfig->AHBBuyer = false;
            _hordeConfig->AHBBuyer    = false;
            _neutralConfig->AHBBuyer  = false;
        }
        else
        {
            _allianceConfig->AHBBuyer = true;
            _hordeConfig->AHBBuyer    = true;
            _neutralConfig->AHBBuyer  = true;
        }

        break;
    }
    case AHBotCommand::seller:
    {
        char* param1 = strtok(args, " ");
        uint32 state = (uint32)strtoul(param1, NULL, 0);

        if (state == 0)
        {
            _allianceConfig->AHBSeller = false;
            _hordeConfig->AHBSeller    = false;
            _neutralConfig->AHBSeller  = false;
        }
        else
        {
            _allianceConfig->AHBSeller = true;
            _hordeConfig->AHBSeller    = true;
            _neutralConfig->AHBSeller  = true;
        }

        break;
    }
    case AHBotCommand::useMarketPrice:
    {
        char* param1 = strtok(args, " ");
        uint32 state = (uint32)strtoul(param1, NULL, 0);

        if (state == 0)
        {
            _allianceConfig->SellAtMarketPrice = false;
            _hordeConfig->SellAtMarketPrice    = false;
            _neutralConfig->SellAtMarketPrice  = false;
        }
        else
        {
            _allianceConfig->SellAtMarketPrice = true;
            _hordeConfig->SellAtMarketPrice    = true;
            _neutralConfig->SellAtMarketPrice  = true;
        }

        break;
    }
    case AHBotCommand::ahexpire:
    {
        AuctionHouseObject* auctionHouse = sAuctionMgr->GetAuctionsMap(config->GetAHFID());

        AuctionHouseObject::AuctionEntryMap::iterator itr;
        itr = auctionHouse->GetAuctionsBegin();

        //
        // Iterate through all the autions and if they belong to the bot, make them expired
        //

        while (itr != auctionHouse->GetAuctionsEnd())
        {
            if (itr->second->owner.GetCounter() == _id)
            {
                // Expired NOW.
                itr->second->expire_time = GameTime::GetGameTime().count();

                uint32 id                = itr->second->Id;
                uint32 expire_time       = itr->second->expire_time;

                CharacterDatabase.Execute("UPDATE auctionhouse SET time = '{}' WHERE id = '{}'", expire_time, id);
            }

            ++itr;
        }

        break;
    }
    case AHBotCommand::minitems:
    {
        char * param1   = strtok(args, " ");
        uint32 minItems = (uint32) strtoul(param1, NULL, 0);

        WorldDatabase.Execute("UPDATE mod_auctionhousebot SET minitems = '{}' WHERE auctionhouse = '{}'", minItems, ahMapID);

        config->SetMinItems(minItems);

        break;
    }
    case AHBotCommand::maxitems:
    {
        char * param1   = strtok(args, " ");
        uint32 maxItems = (uint32) strtoul(param1, NULL, 0);

        WorldDatabase.Execute("UPDATE mod_auctionhousebot SET maxitems = '{}' WHERE auctionhouse = '{}'", maxItems, ahMapID);

        config->SetMaxItems(maxItems);
        config->CalculatePercents();
        break;
    }
    case AHBotCommand::percentages:
    {
        char * param1   = strtok(args, " ");
        char * param2   = strtok(NULL, " ");
        char * param3   = strtok(NULL, " ");
        char * param4   = strtok(NULL, " ");
        char * param5   = strtok(NULL, " ");
        char * param6   = strtok(NULL, " ");
        char * param7   = strtok(NULL, " ");
        char * param8   = strtok(NULL, " ");
        char * param9   = strtok(NULL, " ");
        char * param10  = strtok(NULL, " ");
        char * param11  = strtok(NULL, " ");
        char * param12  = strtok(NULL, " ");
        char * param13  = strtok(NULL, " ");
        char * param14  = strtok(NULL, " ");

        uint32 greytg   = (uint32) strtoul(param1, NULL, 0);
        uint32 whitetg  = (uint32) strtoul(param2, NULL, 0);
        uint32 greentg  = (uint32) strtoul(param3, NULL, 0);
        uint32 bluetg   = (uint32) strtoul(param4, NULL, 0);
        uint32 purpletg = (uint32) strtoul(param5, NULL, 0);
        uint32 orangetg = (uint32) strtoul(param6, NULL, 0);
        uint32 yellowtg = (uint32) strtoul(param7, NULL, 0);
        uint32 greyi    = (uint32) strtoul(param8, NULL, 0);
        uint32 whitei   = (uint32) strtoul(param9, NULL, 0);
        uint32 greeni   = (uint32) strtoul(param10, NULL, 0);
        uint32 bluei    = (uint32) strtoul(param11, NULL, 0);
        uint32 purplei  = (uint32) strtoul(param12, NULL, 0);
        uint32 orangei  = (uint32) strtoul(param13, NULL, 0);
        uint32 yellowi  = (uint32) strtoul(param14, NULL, 0);

        //
        // Setup the percentage in the configuration first, so validity test can be performed
        //

        config->SetPercentages(greytg, whitetg, greentg, bluetg, purpletg, orangetg, yellowtg, greyi, whitei, greeni, bluei, purplei, orangei, yellowi);

        //
        // Save the results into the database (after the tests)
        //

        auto trans = WorldDatabase.BeginTransaction();

        trans->Append("UPDATE mod_auctionhousebot SET percentgreytradegoods   = '{}' WHERE auctionhouse = '{}'", config->GetPercentages(AHB_GREY_TG)  , ahMapID);
        trans->Append("UPDATE mod_auctionhousebot SET percentwhitetradegoods  = '{}' WHERE auctionhouse = '{}'", config->GetPercentages(AHB_WHITE_TG) , ahMapID);
        trans->Append("UPDATE mod_auctionhousebot SET percentgreentradegoods  = '{}' WHERE auctionhouse = '{}'", config->GetPercentages(AHB_GREEN_TG) , ahMapID);
        trans->Append("UPDATE mod_auctionhousebot SET percentbluetradegoods   = '{}' WHERE auctionhouse = '{}'", config->GetPercentages(AHB_BLUE_TG)  , ahMapID);
        trans->Append("UPDATE mod_auctionhousebot SET percentpurpletradegoods = '{}' WHERE auctionhouse = '{}'", config->GetPercentages(AHB_PURPLE_TG), ahMapID);
        trans->Append("UPDATE mod_auctionhousebot SET percentorangetradegoods = '{}' WHERE auctionhouse = '{}'", config->GetPercentages(AHB_ORANGE_TG), ahMapID);
        trans->Append("UPDATE mod_auctionhousebot SET percentyellowtradegoods = '{}' WHERE auctionhouse = '{}'", config->GetPercentages(AHB_YELLOW_TG), ahMapID);
        trans->Append("UPDATE mod_auctionhousebot SET percentgreyitems        = '{}' WHERE auctionhouse = '{}'", config->GetPercentages(AHB_GREY_I)   , ahMapID);
        trans->Append("UPDATE mod_auctionhousebot SET percentwhiteitems       = '{}' WHERE auctionhouse = '{}'", config->GetPercentages(AHB_WHITE_I)  , ahMapID);
        trans->Append("UPDATE mod_auctionhousebot SET percentgreenitems       = '{}' WHERE auctionhouse = '{}'", config->GetPercentages(AHB_GREEN_I)  , ahMapID);
        trans->Append("UPDATE mod_auctionhousebot SET percentblueitems        = '{}' WHERE auctionhouse = '{}'", config->GetPercentages(AHB_BLUE_I)   , ahMapID);
        trans->Append("UPDATE mod_auctionhousebot SET percentpurpleitems      = '{}' WHERE auctionhouse = '{}'", config->GetPercentages(AHB_PURPLE_I) , ahMapID);
        trans->Append("UPDATE mod_auctionhousebot SET percentorangeitems      = '{}' WHERE auctionhouse = '{}'", config->GetPercentages(AHB_ORANGE_I) , ahMapID);
        trans->Append("UPDATE mod_auctionhousebot SET percentyellowitems      = '{}' WHERE auctionhouse = '{}'", config->GetPercentages(AHB_YELLOW_I) , ahMapID);

        WorldDatabase.CommitTransaction(trans);

        break;
    }
    case AHBotCommand::minprice:
    {
        char * param1   = strtok(args, " ");
        uint32 minPrice = (uint32) strtoul(param1, NULL, 0);

        WorldDatabase.Execute("UPDATE mod_auctionhousebot SET minprice{} = '{}' WHERE auctionhouse = '{}'", color, minPrice, ahMapID);

        config->SetMinPrice(col, minPrice);

        break;
    }
    case AHBotCommand::maxprice:
    {
        char * param1   = strtok(args, " ");
        uint32 maxPrice = (uint32) strtoul(param1, NULL, 0);

        WorldDatabase.Execute("UPDATE mod_auctionhousebot SET maxprice{} = '{}' WHERE auctionhouse = '{}'", color, maxPrice, ahMapID);

        config->SetMaxPrice(col, maxPrice);

        break;
    }
    case AHBotCommand::minbidprice:
    {
        char * param1      = strtok(args, " ");
        uint32 minBidPrice = (uint32) strtoul(param1, NULL, 0);

        WorldDatabase.Execute("UPDATE mod_auctionhousebot SET minbidprice{} = '{}' WHERE auctionhouse = '{}'", color, minBidPrice, ahMapID);

        config->SetMinBidPrice(col, minBidPrice);

        break;
    }
    case AHBotCommand::maxbidprice:
    {
        char * param1      = strtok(args, " ");
        uint32 maxBidPrice = (uint32) strtoul(param1, NULL, 0);

        WorldDatabase.Execute("UPDATE mod_auctionhousebot SET maxbidprice{} = '{}' WHERE auctionhouse = '{}'", color, maxBidPrice, ahMapID);

        config->SetMaxBidPrice(col, maxBidPrice);

        break;
    }
    case AHBotCommand::maxstack:
    {
        char * param1   = strtok(args, " ");
        uint32 maxStack = (uint32) strtoul(param1, NULL, 0);

        WorldDatabase.Execute("UPDATE mod_auctionhousebot SET maxstack{} = '{}' WHERE auctionhouse = '{}'", color, maxStack, ahMapID);

        config->SetMaxStack(col, maxStack);

        break;
    }
    case AHBotCommand::buyerprice:
    {
        char * param1     = strtok(args, " ");
        uint32 buyerPrice = (uint32) strtoul(param1, NULL, 0);

        WorldDatabase.Execute("UPDATE mod_auctionhousebot SET buyerprice{} = '{}' WHERE auctionhouse = '{}'", color, buyerPrice, ahMapID);

        config->SetBuyerPrice(col, buyerPrice);

        break;
    }
    /*
    case AHBotCommand::bidinterval:
    {
        char * param1      = strtok(args, " ");
        uint32 bidInterval = (uint32) strtoul(param1, NULL, 0);

        WorldDatabase.Execute("UPDATE mod_auctionhousebot SET buyerbiddinginterval = '{}' WHERE auctionhouse = '{}'", bidInterval, ahMapID);

        config->SetBiddingInterval(bidInterval);

        break;
    }
    case AHBotCommand::bidsperinterval:
    {
        char * param1          = strtok(args, " ");
        uint32 bidsPerInterval = (uint32) strtoul(param1, NULL, 0);

        WorldDatabase.Execute("UPDATE mod_auctionhousebot SET buyerbidsperinterval = '{}' WHERE auctionhouse = '{}'", bidsPerInterval, ahMapID);

        config->SetBidsPerInterval(bidsPerInterval);

        break;
    }
    */
    default:
        break;
    }
}

// =============================================================================
// Initialization of the bot
// =============================================================================

void AuctionHouseBot::Initialize(AHBConfig* allianceConfig, AHBConfig* hordeConfig, AHBConfig* neutralConfig)
{
    _allianceConfig = allianceConfig;
    _hordeConfig = hordeConfig;
    _neutralConfig = neutralConfig;

    _allianceConfig->LoadPriceOverrides();
    _hordeConfig->LoadPriceOverrides();
    _neutralConfig->LoadPriceOverrides();
}

// Helper function to join GUIDs into a comma-separated string
std::string JoinGUIDs(const std::vector<uint32>& guids)
{
    std::ostringstream oss;
    for (size_t i = 0; i < guids.size(); ++i)
    {
        if (i != 0)
        {
            oss << ",";
        }
        oss << guids[i];
    }
    return oss.str();
}

// Helper function to calculate the median
uint64 AuctionHouseBot::CalculateMedian(std::vector<uint64>& prices)
{
    std::sort(prices.begin(), prices.end());
    size_t size = prices.size();
    if (size % 2 == 0)
    {
        return (prices[size / 2 - 1] + prices[size / 2]) / 2;
    }
    else
    {
        return prices[size / 2];
    }
}

// Function to fetch recent auction history and calculate moving average prices
std::pair<uint64, uint64> AuctionHouseBot::CalculateMovingAveragePrices(uint32 itemId, AHBConfig* config)
{
    uint64 totalBuyoutPrice = 0;
    uint64 totalBidPrice = 0;
    uint32 buyoutCount = 0;
    uint32 bidCount = 0;
    std::vector<uint64> buyoutPrices;
    std::vector<uint64> bidPrices;

    QueryResult result;

    if (config->UseAuctionCount)
    {
        // Fetch the last N auctions
        result = WorldDatabase.Query(
            "SELECT final_price, auction_type FROM mod_auctionhousebot_auction_history "
            "WHERE item_id = {} ORDER BY timestamp DESC LIMIT {}", itemId, config->AuctionCount);
    }
    else
    {
        // Fetch auctions from the last N days
        result = WorldDatabase.Query(
            "SELECT final_price, auction_type FROM mod_auctionhousebot_auction_history "
            "WHERE item_id = {} AND timestamp >= NOW() - INTERVAL {} DAY", itemId, config->Days);
    }

    if (result)
    {
        do
        {
            Field* fields = result->Fetch();
            uint64 finalPrice = fields[0].Get<uint64>();
            std::string auctionType = fields[1].Get<std::string>();

            if (auctionType == "buyout")
            {
                buyoutPrices.push_back(finalPrice);
                totalBuyoutPrice += finalPrice;
                buyoutCount++;
            }
            else if (auctionType == "bid")
            {
                bidPrices.push_back(finalPrice);
                totalBidPrice += finalPrice;
                bidCount++;
            }
        } while (result->NextRow());
    }

    // Filter out outliers if enabled
    if (config->FilterOutliers)
    {
        auto filterOutliers = [](std::vector<uint64>& prices) {
            if (!prices.empty())
            {
                uint64 median = CalculateMedian(prices);
                prices.erase(std::remove_if(prices.begin(), prices.end(),
                                            [median](uint64 price) { return price > 2 * median || price < median / 2; }),
                             prices.end());
            }
        };

        filterOutliers(buyoutPrices);
        filterOutliers(bidPrices);

        totalBuyoutPrice = std::accumulate(buyoutPrices.begin(), buyoutPrices.end(), uint64(0));
        buyoutCount = buyoutPrices.size();

        totalBidPrice = std::accumulate(bidPrices.begin(), bidPrices.end(), uint64(0));
        bidCount = bidPrices.size();
    }

    // Weight recent auctions more heavily if enabled
    if (config->WeightRecent)
    {
        auto weightPrices = [](const std::vector<uint64>& prices, uint64& totalPrice) {
            uint64 weightedPrice = 0;
            uint32 weight = 1;
            uint32 totalWeight = 0;

            for (auto it = prices.rbegin(); it != prices.rend(); ++it)
            {
                weightedPrice += (*it) * weight;
                totalWeight += weight;
                weight++;
            }
            totalPrice = (totalWeight > 0) ? weightedPrice / totalWeight : totalPrice;
        };

        weightPrices(buyoutPrices, totalBuyoutPrice);
        weightPrices(bidPrices, totalBidPrice);
    }

    uint64 averageBuyoutPrice = (buyoutCount > 0) ? totalBuyoutPrice / buyoutCount : 0;
    uint64 averageBidPrice = (bidCount > 0) ? totalBidPrice / bidCount : 0;

    // Fetch avgPrice and minPrice values from mod_auctionhousebot_priceOverride table
    QueryResult priceOverrideResult = WorldDatabase.Query(
        "SELECT avgPrice, minPrice FROM mod_auctionhousebot_priceOverride WHERE item = {}", itemId);

    if (priceOverrideResult)
    {
        Field* fields = priceOverrideResult->Fetch();
        uint64 avgPrice = fields[0].Get<uint64>();
        uint64 minPrice = fields[1].Get<uint64>();

        // Enforce minimum and maximum price limits based on avgPrice and minPrice values
        uint64 maxPrice = avgPrice * 2; // Example: max price is twice the average price
        uint64 adjustedMinPrice = minPrice * config->MinPriceTolerance; // Allow prices to go slightly below minPrice
        averageBuyoutPrice = std::clamp(averageBuyoutPrice, adjustedMinPrice, maxPrice);
        averageBidPrice = std::clamp(averageBidPrice, adjustedMinPrice, maxPrice);
    }

    return {averageBuyoutPrice, averageBidPrice};
}

// Function to adjust prices based on moving average prices
void AuctionHouseBot::AdjustPrices(uint32 itemId, uint64& buyoutPrice, uint64& bidPrice, AHBConfig* config)
{
    auto [averageBuyoutPrice, averageBidPrice] = CalculateMovingAveragePrices(itemId, config);

    if (averageBuyoutPrice > 0)
    {
        buyoutPrice = averageBuyoutPrice;
    }

    if (averageBidPrice > 0)
    {
        bidPrice = averageBidPrice;
    }

    // Enforce minimum and maximum price limits based on avgPrice and minPrice values
    QueryResult priceOverrideResult = WorldDatabase.Query(
        "SELECT avgPrice, minPrice FROM mod_auctionhousebot_priceOverride WHERE item = {}", itemId);

    if (priceOverrideResult)
    {
        Field* fields = priceOverrideResult->Fetch();
        uint64 avgPrice = fields[0].Get<uint64>();
        uint64 minPrice = fields[1].Get<uint64>();

        uint64 maxPrice = avgPrice * 2; // Example: max price is twice the average price
        uint64 adjustedMinPrice = minPrice * config->MinPriceTolerance; // Allow prices to go slightly below minPrice
        buyoutPrice = std::clamp(buyoutPrice, adjustedMinPrice, maxPrice);
        bidPrice = std::clamp(bidPrice, adjustedMinPrice, maxPrice);
    }
}

void AuctionHouseBot::CleanupOldAuctionHistory()
{
    AHBConfig* config = nullptr;

    if (_allianceConfig)
    {
        config = _allianceConfig;
    }
    else if (_hordeConfig)
    {
        config = _hordeConfig;
    }
    else if (_neutralConfig)
    {
        config = _neutralConfig;
    }

    if (!config)
    {
        return; // No valid configuration found
    }

    // Read the last cleanup time from the database
    QueryResult result = WorldDatabase.Query("SELECT UNIX_TIMESTAMP(last_cleanup_time) FROM mod_auctionhousebot_cleanup_time WHERE id = 1");
    if (result)
    {
        Field* fields = result->Fetch();
        _lastCleanupTime = static_cast<time_t>(fields[0].Get<uint64>());
    }
    else
    {
        // If no record exists, insert a new one with the current time
        WorldDatabase.Execute("INSERT INTO mod_auctionhousebot_cleanup_time (id, last_cleanup_time) VALUES (1, FROM_UNIXTIME({}))", time(NULL));
        _lastCleanupTime = time(NULL);
    }

    time_t currentTime = time(NULL);

    // Perform cleanup every 24 hours
    if (difftime(currentTime, _lastCleanupTime) >= 24 * 60 * 60)
    {
        if (config->UseAuctionCount)
        {
            // Keep the last N auctions for each item
            WorldDatabase.Execute(
                "DELETE FROM `mod_auctionhousebot_auction_history` "
                "WHERE id NOT IN ("
                "SELECT id FROM ("
                "SELECT id FROM `mod_auctionhousebot_auction_history` "
                "ORDER BY `timestamp` DESC "
                "LIMIT {}"
                ") AS temp)", config->AuctionCount);
        }
        else
        {
            // Remove auction history entries older than the specified number of days
            WorldDatabase.Execute(
                "DELETE FROM `mod_auctionhousebot_auction_history` "
                "WHERE `timestamp` < NOW() - INTERVAL {} DAY", config->Days);
        }

        // Update the last cleanup time in the database
        WorldDatabase.Execute("UPDATE mod_auctionhousebot_cleanup_time SET last_cleanup_time = FROM_UNIXTIME({}) WHERE id = 1", currentTime);
        _lastCleanupTime = currentTime;
    }
}
