<?php

function readJsonFile(string $filePath): ?array
{
    if (!file_exists($filePath)) {
        return null;
    }

    $content = file_get_contents($filePath);
    if ($content === false) {
        return null;
    }

    $data = json_decode($content, true);
    if (json_last_error() !== JSON_ERROR_NONE) {
        return null;
    }

    return $data;
}

// Example usage:
// $data = readJsonFile('data.json');
// if ($data !== null) {
//     print_r($data);
// } else {
//     echo 'Failed to read JSON file';
// }
