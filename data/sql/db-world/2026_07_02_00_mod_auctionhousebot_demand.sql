SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- ----------------------------
-- Table structure for mod_auctionhousebot_demand
-- ----------------------------
DROP TABLE IF EXISTS `mod_auctionhousebot_demand`;
CREATE TABLE `mod_auctionhousebot_demand`  (
  `item` int(10) UNSIGNED NOT NULL,
  `multiplier` double NOT NULL DEFAULT 1.0,
  `last_bump` bigint(20) NOT NULL DEFAULT 0,
  PRIMARY KEY (`item`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci ROW_FORMAT = Dynamic;
