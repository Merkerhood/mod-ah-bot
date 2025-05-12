/*
 * Copyright (C) 2016+ AzerothCore <www.azerothcore.org>, released under GNU AGPL v3 license: https://github.com/azerothcore/azerothcore-wotlk/blob/master/LICENSE
 */

#include "Config.h"
#include "Log.h"

#include "AuctionHouseBot.h"
#include "AuctionHouseBotCommon.h"
#include "AuctionHouseBotWorldScript.h"

// =============================================================================
// Initialization of the bot during the world startup
// =============================================================================

AHBot_WorldScript::AHBot_WorldScript() : WorldScript("AHBot_WorldScript", {
    WORLDHOOK_ON_BEFORE_CONFIG_LOAD,
    WORLDHOOK_ON_STARTUP
})
{

}

void AHBot_WorldScript::OnBeforeConfigLoad(bool reload)
{
    // Retrieve how many bots shall be operating on the auction market
    bool debug = sConfigMgr->GetOption<bool>("AuctionHouseBot.DEBUG", false);
    bool UsePlayerbotsAsAHBots = sConfigMgr->GetOption<bool>("AuctionHouseBot.UsePlayerbotsAsAHBots", false);

    std::vector<uint32> botGUIDs;
    uint32 account = sConfigMgr->GetOption<uint32>("AuctionHouseBot.Account", 0);

    if(UsePlayerbotsAsAHBots)
    {
        std::string playerBotsAccountPrefix = sConfigMgr->GetOption<std::string>("AuctionHouseBot.PlayerBotsAccountPrefix", "RNDBOT");

        // Retrieve list of playerbots GUIDs from the database
        QueryResult result = LoginDatabase.Query(
            "SELECT guid FROM characters WHERE account IN (SELECT id FROM account WHERE username LIKE '{}%')",
            playerBotsAccountPrefix);

        if (result)
        {
            do
            {
                Field* fields = result->Fetch();
                uint32 botGuid = fields[0].Get<uint32>();
                botGUIDs.push_back(botGuid);

                if (debug)
                {
                    LOG_INFO("server.loading", "AHBot: Adding playerbot GUID {}", botGuid);
                }
            } while (result->NextRow());
        }
        else
        {
            LOG_ERROR("server.loading", "AHBot: No playerbots found with prefix '{}'", playerBotsAccountPrefix);
        }

    }
    else
    {
        // Retrieve list of GUIDs from the configuration
        std::string guidsStr = sConfigMgr->GetOption<std::string>("AuctionHouseBot.GUIDs", "");
        std::stringstream ss(guidsStr);
        std::string guid;

        while (std::getline(ss, guid, ','))
        {
            try
            {
                botGUIDs.push_back(std::stoul(guid));
            }
            catch (const std::exception& e)
            {
                LOG_ERROR("server.loading", "AHBot: Invalid GUID '{}' in configuration: {}", guid, e.what());
            }
        }

        if (debug)
        {
            LOG_INFO("module", "AHBot: Player bots will not be used, instead we will use the fallback account {} and GUIDs {}", account, guidsStr);
        }
    }

    // Validate that we have either an account or bot GUIDs
    if (account == 0 && botGUIDs.empty())
    {
        LOG_ERROR("server.loading", "AHBot: Account id and GUIDs list missing from configuration; is that the right file?");
        return;
    }

    gBotsId.clear();

    // Clear the global bot ID set
    gBotsId.clear();

    // Add GUIDs to the global bot ID set
    for (uint32 botId : botGUIDs)
    {
        gBotsId.insert(botId);

        if (debug)
        {
            LOG_INFO("server.loading", "AHBot: Adding bot GUID {}", botId);
        }
    }

    // If no GUIDs were added, fallback to querying all characters of the account
    if (gBotsId.empty() && account != 0)
    {
        QueryResult result = CharacterDatabase.Query("SELECT guid FROM characters WHERE account = {}", account);

        if (result)
        {
            do
            {
                Field* fields = result->Fetch();
                uint32 botId = fields[0].Get<uint32>();
                gBotsId.insert(botId);

                if (debug)
                {
                    LOG_INFO("server.loading", "AHBot: Adding bot GUID {} from account {}", botId, account);
                }
            } while (result->NextRow());
        }
        else
        {
            LOG_ERROR("server.loading", "AHBot: Could not query the database for characters of account {}", account);
            return;
        }
    }

    // Ensure we have at least one bot GUID
    if (gBotsId.empty())
    {
        LOG_ERROR("server.loading", "AHBot: No characters registered for account {}", account);
        return;
    }

    // Start the bots only if the operation is a reload, otherwise let the OnStartup do the job
    if (reload)
    {
        if (debug)
        {
            LOG_INFO("module", "AHBot: Reloading the bots");
        }

        // Clear the bots array; this way they wont be used anymore during the initialization stage.
        DeleteBots();

        // Reload the configuration for the auction houses
        gAllianceConfig->Initialize(gBotsId);
        gHordeConfig->Initialize(gBotsId);
        gNeutralConfig->Initialize(gBotsId);

        // Start again the bots
        PopulateBots();
    }
}

void AHBot_WorldScript::OnStartup()
{
    LOG_INFO("server.loading", "Initialize AuctionHouseBot...");

    //
    // Initialize the configuration (done only once at startup)
    //

    gAllianceConfig->Initialize(gBotsId);
    gHordeConfig->Initialize   (gBotsId);
    gNeutralConfig->Initialize (gBotsId);

    //
    // Starts the bots
    //

    PopulateBots();
}

void AHBot_WorldScript::DeleteBots()
{
    //
    // Save the old bots references.
    //

    std::set<AuctionHouseBot*> oldBots;

    for (AuctionHouseBot* bot: gBots)
    {
        oldBots.insert(bot);
    }

    //
    // Clear the bot list
    //

    gBots.clear();

    //
    // Free the resources used up by the old bots
    //

    for (AuctionHouseBot* bot: oldBots)
    {
        delete bot;
    }
}


void AHBot_WorldScript::PopulateBots()
{
    uint32 account = sConfigMgr->GetOption<uint32>("AuctionHouseBot.Account", 0);

    // Insert the bot in the list used for auction house iterations
    gBots.clear();

    gNeutralConfig->LoadBotGUIDs();
    // there is not difference between the configs yet, so no need to load them separately
    gAllianceConfig->LoadBotGUIDs();
    gHordeConfig->LoadBotGUIDs();

    const std::vector<uint32>& botGUIDs = gNeutralConfig->GetBotGUIDs(); // Assuming all configs have the same GUIDs

    for (uint32 guid : botGUIDs)
    {
        AuctionHouseBot* bot = new AuctionHouseBot(account, guid);
        bot->Initialize(gAllianceConfig, gHordeConfig, gNeutralConfig);
        gBots.insert(bot);
    }
}
