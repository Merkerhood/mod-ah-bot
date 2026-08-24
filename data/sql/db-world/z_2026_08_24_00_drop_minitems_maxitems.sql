--
-- Drop the unused minitems / maxitems columns from mod_auctionhousebot.
--
-- The module stopped reading them a long time ago: InitializeFromSql() had both
-- SELECTs commented out, so the seller took its ceiling from AuctionHouseBot.MaxItems
-- in the conf file only, while the columns kept being written by ".ahbotoptions
-- minitems/maxitems" and never read back. Editing them looked like it worked and did
-- nothing.
--
-- Guarded so the file is safe to run twice and works on both MySQL and MariaDB
-- (MySQL has no DROP COLUMN IF EXISTS). The z_ prefix keeps it sorted after
-- mod_auctionhousebot.sql, which still creates the columns on a fresh install.
--

SET @columnExists := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME   = 'mod_auctionhousebot'
      AND COLUMN_NAME  = 'minitems');

SET @statement := IF(@columnExists > 0,
    'ALTER TABLE `mod_auctionhousebot` DROP COLUMN `minitems`',
    'DO 0');

PREPARE dropMinItems FROM @statement;
EXECUTE dropMinItems;
DEALLOCATE PREPARE dropMinItems;

SET @columnExists := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME   = 'mod_auctionhousebot'
      AND COLUMN_NAME  = 'maxitems');

SET @statement := IF(@columnExists > 0,
    'ALTER TABLE `mod_auctionhousebot` DROP COLUMN `maxitems`',
    'DO 0');

PREPARE dropMaxItems FROM @statement;
EXECUTE dropMaxItems;
DEALLOCATE PREPARE dropMaxItems;
