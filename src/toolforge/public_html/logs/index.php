<?php
/** Show shared high-level project logs. */
declare(strict_types=1);

require dirname(__DIR__, 2) . '/logs.php';
serveLogs('project');
