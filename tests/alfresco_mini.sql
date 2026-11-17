-- Sous-ensemble du schéma PostgreSQL d'Alfresco (noms de colonnes réels), pour tester les requêtes.
CREATE TABLE alf_namespace (id bigint PRIMARY KEY, version bigint, uri varchar(100));
CREATE TABLE alf_qname (id bigint PRIMARY KEY, version bigint, ns_id bigint, local_name varchar(200));
CREATE TABLE alf_store (id bigint PRIMARY KEY, version bigint, protocol varchar(50), identifier varchar(100), root_node_id bigint);
CREATE TABLE alf_node (id bigint PRIMARY KEY, version bigint, store_id bigint, uuid varchar(36), transaction_id bigint,
  type_qname_id bigint, locale_id bigint, acl_id bigint, audit_creator varchar(255), audit_created varchar(30),
  audit_modifier varchar(255), audit_modified varchar(30), audit_accessed varchar(30));
CREATE TABLE alf_child_assoc (id bigint PRIMARY KEY, version bigint, parent_node_id bigint, type_qname_id bigint,
  child_node_name_crc bigint, child_node_name varchar(50), child_node_id bigint, qname_ns_id bigint,
  qname_localname varchar(255), qname_crc bigint, is_primary boolean, assoc_index int);
CREATE TABLE alf_node_properties (node_id bigint, actual_type_n int, persisted_type_n int, boolean_value boolean,
  long_value bigint, float_value float, double_value float, string_value varchar(1024), serializable_value bytea,
  qname_id bigint, list_index int, locale_id bigint);
INSERT INTO alf_namespace VALUES (1,0,'http://www.alfresco.org/model/content/1.0'),(2,0,'https://ceiba-analytics.com/model/mulcv/1.0'),
  (3,0,'http://www.alfresco.org/model/system/1.0');
INSERT INTO alf_qname VALUES (10,0,1,'folder'),(11,0,1,'content'),(12,0,1,'thumbnail'),(13,0,1,'person'),(14,0,1,'name'),
  (15,0,1,'userName'),(16,0,1,'firstName'),(17,0,1,'lastName'),(20,0,2,'dossier'),(21,0,2,'plan'),(22,0,2,'qualityStatus'),
  (23,0,2,'classCode'),(24,0,2,'dossierId'),(25,0,2,'boxReference'),(26,0,3,'name');
INSERT INTO alf_store VALUES (1,0,'workspace','SpacesStore',1),(2,0,'archive','SpacesStore',99);
INSERT INTO alf_node(id,store_id,uuid,type_qname_id,audit_creator,audit_created,audit_modifier,audit_modified) VALUES
 (1,1,'root',10,'System','2026-01-01T00:00:00.000Z','System','2026-01-01T00:00:00.000Z'),
 (2,1,'site-mulcv',10,'admin','2026-01-01T00:00:00.000Z','admin','2026-01-01T00:00:00.000Z'),
 (3,1,'doclib',10,'admin','2026-01-01T00:00:00.000Z','admin','2026-01-01T00:00:00.000Z'),
 (4,1,'20bb0608-427e-40e7-bb06-08427eb0e773',10,'admin','2026-01-01T00:00:00.000Z','admin','2026-01-01T00:00:00.000Z'),
 (5,1,'prod',10,'a','2026-01-01T00:00:00.000Z','a','2026-01-01T00:00:00.000Z'),
 (6,1,'zone',10,'a','2026-01-01T00:00:00.000Z','a','2026-01-01T00:00:00.000Z'),
 (7,1,'cnt',10,'a','2026-01-01T00:00:00.000Z','a','2026-01-01T00:00:00.000Z'),
 (8,1,'carton',10,'a','2026-01-01T00:00:00.000Z','a','2026-01-01T00:00:00.000Z'),
 (9,1,'dossier',20,'a','2026-01-01T00:00:00.000Z','a','2026-01-01T00:00:00.000Z'),
 (10,1,'aaaaaaaa-0000-0000-0000-000000000001',21,'service-account-mulcv-depot-poste1','2026-11-17T08:01:02.345Z','awa','2026-11-17T09:00:00.000Z'),
 (11,1,'aaaaaaaa-0000-0000-0000-000000000002',11,'service-account-mulcv-depot-poste2','2026-11-17T08:05:00.000Z','service-account-mulcv-scan-worker','2026-11-17T08:06:00.000Z'),
 (12,1,'thumb',12,'System','2026-11-17T08:06:00.000Z','System','2026-11-17T08:06:00.000Z'),
 (13,1,'bbbbbbbb-0000-0000-0000-000000000003',11,'service-account-mulcv-depot-poste1','2026-11-16T10:00:00.000Z','awa','2026-11-16T11:00:00.000Z'),
 (14,1,'person-awa',13,'admin','2026-01-01T00:00:00.000Z','admin','2026-01-01T00:00:00.000Z'),
 (15,2,'person-old',13,'admin','2026-01-01T00:00:00.000Z','admin','2026-01-01T00:00:00.000Z');
INSERT INTO alf_child_assoc(id,parent_node_id,child_node_id,qname_localname,is_primary) VALUES
 (1,1,2,'mulcv',true),(2,2,3,'documentLibrary',true),(3,3,4,'Scan',true),(4,4,5,'Production',true),(5,5,6,'Zone_Sud',true),
 (6,6,7,'CNT-0001',true),(7,7,8,'BOX_x0020_2',true),(8,8,9,'FON-3302-113-0001-012',true),(9,9,10,'piece1.pdf',true),
 (10,9,11,'piece2.pdf',true),(11,11,12,'doclib',true),(12,99,13,'ailleurs',true);
INSERT INTO alf_node_properties(node_id,qname_id,string_value) VALUES
 (10,14,'FON-3302-113-0001-012-P01.pdf'),(10,22,'VERIFIED'),(10,23,'3302'),(10,24,'FON-3302-113-0001-012'),(10,26,'sysname'),
 (11,14,'FON-3302-113-0001-012-P02.pdf'),(11,22,'REVIEW_REQUIRED'),
 (13,14,'verse.pdf'),(13,22,'VERIFIED'),
 (14,15,'awa'),(14,16,'Awa'),(14,17,'DIALLO'),(15,15,'old');
