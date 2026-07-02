/*
 * Copyright (C) 2016+ AzerothCore <www.azerothcore.org>, released under GNU AGPL v3 license: https://github.com/azerothcore/azerothcore-wotlk/blob/master/LICENSE
 */

#include "AuctionHouseMgr.h"
#include "CharacterCache.h"
#include "Field.h"
#include "GameTime.h"
#include "QueryResult.h"

#include "AuctionHouseBot.h"
#include "AuctionHouseBotCommon.h"
#include "AuctionHouseBotAuctionHouseScript.h"

#include <algorithm>

// Demand multiplier human detection: a buyer counts as human when their character isn't
// one of the bot's own gBotsId characters and their account isn't a mod-playerbots random
// bot (there's no compile-time dependency on mod-playerbots, so this is done by account
// username prefix instead). Resolution failures return false ("don't bump") rather than
// guessing.
static bool IsHumanBuyer(ObjectGuid buyerGuid, AHBConfig* config)
{
    if (buyerGuid.IsEmpty())
    {
        return false;
    }

    if (gBotsId.find(buyerGuid.GetCounter()) != gBotsId.end())
    {
        return false;
    }

    uint32 accountId = sCharacterCache->GetCharacterAccountIdByGuid(buyerGuid);

    if (accountId == 0)
    {
        return false;
    }

    auto cacheIt = gAccountHumanCache.find(accountId);
    if (cacheIt != gAccountHumanCache.end())
    {
        return cacheIt->second;
    }

    QueryResult result = LoginDatabase.Query("SELECT username FROM account WHERE id = {}", accountId);

    if (!result)
    {
        // Ambiguous resolution: don't cache a guess, just don't bump this time.
        return false;
    }

    std::string username = result->Fetch()[0].Get<std::string>();
    std::transform(username.begin(), username.end(), username.begin(), ::tolower);

    bool isHuman = true;

    for (const std::string& prefix : config->DynamicPricingBotAccountPrefixes)
    {
        if (!prefix.empty() && username.compare(0, prefix.size(), prefix) == 0)
        {
            isHuman = false;
            break;
        }
    }

    gAccountHumanCache[accountId] = isHuman;

    return isHuman;
}

AHBot_AuctionHouseScript::AHBot_AuctionHouseScript() : AuctionHouseScript("AHBot_AuctionHouseScript", {
    AUCTIONHOUSEHOOK_ON_BEFORE_AUCTIONHOUSEMGR_SEND_AUCTION_SUCCESSFUL_MAIL,
    AUCTIONHOUSEHOOK_ON_BEFORE_AUCTIONHOUSEMGR_SEND_AUCTION_EXPIRED_MAIL,
    AUCTIONHOUSEHOOK_ON_BEFORE_AUCTIONHOUSEMGR_SEND_AUCTION_OUTBIDDED_MAIL,
    AUCTIONHOUSEHOOK_ON_AUCTION_ADD,
    AUCTIONHOUSEHOOK_ON_AUCTION_REMOVE,
    AUCTIONHOUSEHOOK_ON_AUCTION_SUCCESSFUL,
    AUCTIONHOUSEHOOK_ON_AUCTION_EXPIRE,
    AUCTIONHOUSEHOOK_ON_BEFORE_AUCTIONHOUSEMGR_UPDATE
})
{

}

void AHBot_AuctionHouseScript::OnBeforeAuctionHouseMgrSendAuctionSuccessfulMail(
    AuctionHouseMgr*,                /*auctionHouseMgr*/
    AuctionEntry*,                   /*auction*/
    Player* owner,
    uint32&,                         /*owner_accId*/
    uint32&,                         /*profit*/
    bool& sendNotification,
    bool& updateAchievementCriteria,
    bool&                            /*sendMail*/)
{
    if (owner && gBotsId.find(owner->GetGUID().GetCounter()) != gBotsId.end())
    {
        sendNotification          = false;
        updateAchievementCriteria = false;
    }
}

void AHBot_AuctionHouseScript::OnBeforeAuctionHouseMgrSendAuctionExpiredMail(
    AuctionHouseMgr*,       /* auctionHouseMgr */
    AuctionEntry*,          /* auction */
    Player* owner,
    uint32&,                /* owner_accId */
    bool& sendNotification,
    bool&                   /* sendMail */)
{
    if (owner && gBotsId.find(owner->GetGUID().GetCounter()) != gBotsId.end())
    {
        sendNotification = false;
    }
}

void AHBot_AuctionHouseScript::OnBeforeAuctionHouseMgrSendAuctionOutbiddedMail(
    AuctionHouseMgr*,      /* auctionHouseMgr */
    AuctionEntry* auction,
    Player* oldBidder,
    uint32&,               /* oldBidder_accId */
    Player* newBidder,
    uint32& newPrice,
    bool&,                 /* sendNotification */
    bool&                  /* sendMail */)
{
    if (oldBidder && !newBidder)
    {
        if (gBotsId.size() > 0)
        {
            //
            // Use a random bot id
            //

            uint32 randBot = urand(0, gBotsId.size() - 1);
            std::set<uint32>::iterator it = gBotsId.begin();
            std::advance(it, randBot);

            oldBidder->GetSession()->SendAuctionBidderNotification(
                (uint32)auction->GetHouseId(),
                auction->Id,
                ObjectGuid::Create<HighGuid::Player>(*it),
                newPrice,
                auction->GetAuctionOutBid(),
                auction->item_template);
        }
    }
}

void AHBot_AuctionHouseScript::OnAuctionAdd(AuctionHouseObject* /*ah*/, AuctionEntry* auction)
{
    //
    // The the configuration for the auction house
    //

    AuctionHouseEntry const* ahEntry = sAuctionMgr->GetAuctionHouseEntryFromHouse(auction->GetHouseId());
    AHBConfig* config  = gNeutralConfig;

    if (ahEntry)
    {
        if (AuctionHouseId(ahEntry->houseId) == AuctionHouseId::Alliance)
        {
            config = gAllianceConfig;
        }
        else if (AuctionHouseId(ahEntry->houseId) == AuctionHouseId::Horde)
        {
            config = gHordeConfig;
        }
    }

    //
    // Consider only those auctions handled by the bots
    //

    if (config->ConsiderOnlyBotAuctions)
    {
        if (gBotsId.find(auction->owner.GetCounter()) != gBotsId.end())
        {
            return;
        }
    }

    //
    // Verify if we can operate on the item
    //

    Item* pItem = sAuctionMgr->GetAItem(auction->item_guid);

    if (!pItem)
    {
        if (config->DebugOut)
        {
            LOG_ERROR("module", "AHBot: Item {} for entryiD={} doesn't exist, perhaps bought already?", auction->item_guid.ToString(), auction->Id);
        }

        return;
    }

    //
    // Keeps updated the amount of items in the auction
    //

    ItemTemplate const* prototype = sObjectMgr->GetItemTemplate(auction->item_template);

    config->IncItemCounts(prototype->Class, prototype->Quality);

    if (config->DebugOut)
    {
        LOG_INFO("module", "AHBot: Auction Added ah={}, auctionId={}, totalAHItems = {}", AuctionHouseId(ahEntry->houseId), auction->Id, config->TotalItemCounts());
    }
}

// this is called after the auction has been removed from the DB
void AHBot_AuctionHouseScript::OnAuctionRemove(AuctionHouseObject* /*ah*/, AuctionEntry* auction)
{
    // Get the configuration for the auction house
    //
    AuctionHouseEntry const* ahEntry = sAuctionMgr->GetAuctionHouseEntryFromHouse(auction->GetHouseId());
    AHBConfig* config = gNeutralConfig;

    if (ahEntry)
    {
        if (AuctionHouseId(ahEntry->houseId) == AuctionHouseId::Alliance)
        {
            config = gAllianceConfig;
        }
        else if (AuctionHouseId(ahEntry->houseId) == AuctionHouseId::Horde)
        {
            config = gHordeConfig;
        }
    }

    // Consider only those auctions handled by the bots
    if (config->ConsiderOnlyBotAuctions)
    {
        if (gBotsId.find(auction->owner.GetCounter()) != gBotsId.end())
        {
            return;
        }
    }

    // only get the prototype as actual item has already been removed from server AH in this callback
    ItemTemplate const* prototype = sObjectMgr->GetItemTemplate(auction->item_template);

    if (prototype)
    {
        config->DecItemCounts(prototype->Class, prototype->Quality);
        if (config->DebugOut)
        {
            LOG_INFO("module", "AHBot: Auction removed ah={}, auctionId={}, Bot totalAHItems={}", AuctionHouseId(ahEntry->houseId), auction->Id, config->TotalItemCounts());
        }
    }
    else
    {
        // should never happen
        if (config->DebugOut)
        {
            LOG_ERROR("module", "AHBot: Item was removed but no prototype was found");
        }
    }
}

void AHBot_AuctionHouseScript::OnAuctionSuccessful(AuctionHouseObject* /*ah*/, AuctionEntry* auction)
{
    //
    // Get the configuration for the auction house
    //

    AuctionHouseEntry const* ahEntry = sAuctionMgr->GetAuctionHouseEntryFromHouse(auction->GetHouseId());
    AHBConfig* config = gNeutralConfig;

    if (ahEntry)
    {
        if (AuctionHouseId(ahEntry->houseId) == AuctionHouseId::Alliance)
        {
            config = gAllianceConfig;
        }
        else if (AuctionHouseId(ahEntry->houseId) == AuctionHouseId::Horde)
        {
            config = gHordeConfig;
        }
    }

    //
    // If the auction has been won, it means that it has been accepted by the market.
    // Use the buyout as a reference since the price for the bid is downgraded during selling.
    //

    if (config->DebugOut)
    {
        LOG_INFO("module", "AHBot: Auction successful ah={}, auctionId={}, Bot totalAHItems={}", AuctionHouseId(ahEntry->houseId), auction->Id, config->TotalItemCounts());
    }

    config->UpdateItemStats(auction->item_template, auction->itemCount, auction->buyout);

    // Demand multiplier: only real human purchases nudge the price up.
    if (config->DynamicPricingEnable && IsHumanBuyer(auction->bidder, config))
    {
        config->BumpDemand(auction->item_template);
    }

    // Insert record into auction history table
    std::string auctionType = (auction->bid > 0) ? "bid" : "buyout";
    uint64 finalPrice = (auction->bid > 0) ? auction->bid : auction->buyout;

    auto trans = WorldDatabase.BeginTransaction();
    trans->Append("INSERT INTO `mod_auctionhousebot_auction_history` (`item_id`, `quantity`, `final_price`, `auction_type`, `seller`, `buyer`) VALUES ({}, {}, {}, '{}', {}, {})",
               auction->item_template, auction->itemCount, finalPrice, auctionType.c_str(), auction->owner.GetRawValue(), auction->bidder.GetRawValue());
    WorldDatabase.CommitTransaction(trans);

}

void AHBot_AuctionHouseScript::OnAuctionExpire(AuctionHouseObject* /*ah*/, AuctionEntry* auction)
{
    //
    // Get the configuration for the auction house
    //

    if (!auction)
    {
        LOG_ERROR("module", "AHBot: AHBot_AuctionHouseScript::OnAuctionExpire invalid AuctionEntry");
    }

    AuctionHouseEntry const* ahEntry = sAuctionMgr->GetAuctionHouseEntryFromHouse(auction->GetHouseId());
    AHBConfig* config = gNeutralConfig;

    if (ahEntry)
    {
        if (AuctionHouseId(ahEntry->houseId) == AuctionHouseId::Alliance)
        {
            config = gAllianceConfig;
        }
        else if (AuctionHouseId(ahEntry->houseId) == AuctionHouseId::Horde)
        {
            config = gHordeConfig;
        }
    }

    //
    // If the auction expired, then it means that the bid was unwanted by the market.
    // Bid price is usually less or equal to the buyout, so this likely will bring the price down.
    //

    config->UpdateItemStats(auction->item_template, auction->itemCount, auction->bid);

    // Decrement item counts for the expired auction
    ItemTemplate const* prototype = sObjectMgr->GetItemTemplate(auction->item_template);
    if (prototype)
    {
        config->DecItemCounts(prototype->Class, prototype->Quality);
    }
    else
    {
        LOG_ERROR("module", "AHBot: Item prototype not found for expired auction");
    }

    // Log the updated total item counts
    if (config->DebugOut)
    {
        LOG_INFO("module", "AHBot: Auction Expired ah={}, auctionId={} Bot totalAHItems={}", AuctionHouseId(ahEntry->houseId), auction->Id, config->TotalItemCounts());
    }

    // Insert record into auction history table
    auto trans = WorldDatabase.BeginTransaction();
    trans->Append("INSERT INTO `mod_auctionhousebot_auction_history` (`item_id`, `quantity`, `final_price`, `auction_type`, `seller`, `buyer`) VALUES ({}, {}, {}, 'expired', {}, NULL)",
               auction->item_template, auction->itemCount, auction->startbid, auction->owner.GetRawValue());
    WorldDatabase.CommitTransaction(trans);
}

void AHBot_AuctionHouseScript::OnBeforeAuctionHouseMgrUpdate()
{
    //
    // For every registered bot, perform an update
    //

    for (AuctionHouseBot* bot: gBots)
    {
        bot->Update();
    }
}
