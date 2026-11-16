IF OBJECT_ID(N'[{schema}].schema_history', N'U') IS NULL
CREATE TABLE [{schema}].schema_history (
    installed_rank    INT           NOT NULL PRIMARY KEY,
    version           NVARCHAR(100) NOT NULL UNIQUE,
    description       NVARCHAR(450) NOT NULL,
    script            NVARCHAR(450) NOT NULL,
    checksum          NVARCHAR(100) NOT NULL,
    installed_on      DATETIME2(6)  NOT NULL DEFAULT SYSUTCDATETIME(),
    execution_time_ms INT           NOT NULL
)
