const fs = require('fs');
const path = require('path');

const directoryPath = 'D:\\Master\\code\\stego-side-wing\\datasets\\news_angles';

// Get today's date at midnight for comparison
const today = new Date();
today.setHours(0, 0, 0, 0);

// Read all files in the directory
const files = fs.readdirSync(directoryPath);

// Filter files created today and are JSON files
const todayFiles = files.filter(file => {
    if (!file.endsWith('.json')) {
        return false;
    }
    
    const filePath = path.join(directoryPath, file);
    const stats = fs.statSync(filePath);
    const fileDate = new Date(stats.birthtime);
    fileDate.setHours(0, 0, 0, 0);
    
    return fileDate.getTime() === today.getTime();
});

console.log(`Found ${todayFiles.length} JSON files created today:`);
todayFiles.forEach(file => console.log(`  - ${file}`));

if (todayFiles.length === 0) {
    console.log('No files found created today.');
    process.exit(0);
}

// Read and parse all JSON files
const anglesData = [];
const errors = [];

todayFiles.forEach(file => {
    try {
        const filePath = path.join(directoryPath, file);
        const content = fs.readFileSync(filePath, 'utf8');
        const json = JSON.parse(content);
        
        if (json.angles) {
            anglesData.push({
                file: file,
                angles: json.angles
            });
        } else {
            errors.push(`${file}: No "angles" property found`);
        }
    } catch (error) {
        errors.push(`${file}: Error reading/parsing - ${error.message}`);
    }
});

if (errors.length > 0) {
    console.log('\nErrors:');
    errors.forEach(err => console.log(`  ${err}`));
}

if (anglesData.length === 0) {
    console.log('\nNo files with "angles" property found.');
    process.exit(0);
}

// Compare angles property across all files
console.log(`\nComparing "angles" property across ${anglesData.length} files...`);

// Sort angles for each file before comparison (sort by stringified version for stable comparison)
anglesData.forEach(item => {
    // Create a deep copy and sort
    const sortedAngles = [...item.angles].sort((a, b) => {
        const aStr = JSON.stringify(a);
        const bStr = JSON.stringify(b);
        return aStr.localeCompare(bStr);
    });
    item.sortedAngles = sortedAngles;
});

// Count angles for each file (explicitly count the array)
const anglesCounts = anglesData.map(item => {
    const count = Array.isArray(item.angles) ? item.angles.length : 0;
    return count;
});
const firstCount = anglesCounts[0];
const allCountsIdentical = anglesCounts.every(count => count === firstCount);

console.log(`\nAngles count per file:`);
anglesData.forEach((item, index) => {
    const count = anglesCounts[index];
    console.log(`  ${item.file}: ${count} angles`);
});

if (allCountsIdentical) {
    console.log(`\n✓ Angles COUNT is IDENTICAL across all files: ${firstCount} angles`);
} else {
    console.log(`\n✗ Angles COUNT differs:`);
    const uniqueCounts = [...new Set(anglesCounts)];
    uniqueCounts.forEach(count => {
        const filesWithCount = anglesData.filter((_, i) => anglesCounts[i] === count).map(d => d.file);
        console.log(`  ${count} angles: ${filesWithCount.join(', ')}`);
    });
}

// Convert sorted angles to JSON string for comparison (normalized and sorted)
const anglesStrings = anglesData.map(item => JSON.stringify(item.sortedAngles));

// Check if all are identical
const firstAngles = anglesStrings[0];
const allIdentical = anglesStrings.every(angles => angles === firstAngles);

if (allIdentical) {
    console.log('\n✓ SUCCESS: All "angles" properties are IDENTICAL across all files created today (after sorting).');
    console.log(`\nSample angles structure (from ${anglesData[0].file}):`);
    console.log(JSON.stringify(anglesData[0].sortedAngles, null, 2).substring(0, 500) + '...');
} else {
    console.log('\n✗ DIFFERENCE FOUND: The "angles" properties are NOT identical (even after sorting).');
    
    // Find which files differ
    const uniqueAngles = new Set(anglesStrings);
    console.log(`\nFound ${uniqueAngles.size} unique "angles" structures:`);
    
    anglesData.forEach((item, index) => {
        const anglesStr = anglesStrings[index];
        const matchIndex = anglesStrings.findIndex(s => s === anglesStr);
        if (matchIndex === index) {
            console.log(`\n  Structure ${anglesData.findIndex((_, i) => anglesStrings[i] === anglesStr) + 1}:`);
            console.log(`    Files: ${anglesData.filter((_, i) => anglesStrings[i] === anglesStr).map(d => d.file).join(', ')}`);
            console.log(`    Angles count: ${anglesCounts[index]}`);
        }
    });
}
