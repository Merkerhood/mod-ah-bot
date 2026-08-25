--
-- Let the Shredder Operating Manual pages back onto the auction house.
--
-- z_filter_disabled_and_trash.sql excludes every item whose name contains
-- "manual", which was aimed at the "Manual: ..." / "Manual of ..." teaching items
-- but also caught "Shredder Operating Manual - Page N". Those pages are tradeable
-- world drops that players are supposed to buy and combine themselves, so they
-- belong in the market. That filter file now carries an exception for them; this
-- removes the rows it already inserted.
--
-- The combined "Shredder Operating Manual - Chapter N" items are deliberately NOT
-- touched: they are bound, cannot be traded by a player, and stay blacklisted.
--
-- Idempotent: re-running deletes nothing once the rows are gone. The zz_ prefix
-- keeps it sorted after z_filter_disabled_and_trash.sql.
--

DELETE d
FROM `mod_auctionhousebot_disabled_items` d
JOIN `item_template` i ON i.`entry` = d.`item`
WHERE i.`name` LIKE 'Shredder Operating Manual - Page%';
