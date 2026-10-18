<?php
/** Show detailed Aranami daily logs. */
declare(strict_types=1);

require dirname(__DIR__, 3) . '/logs.php';
serveLogs('aranami');
