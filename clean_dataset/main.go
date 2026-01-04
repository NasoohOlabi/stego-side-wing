package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"sync"
	"sync/atomic"
	"time"
)

// CONFIGURATION
// const (
//
//	datasetPath        = "../datasets/javahelp"
//	cleanedDatasetPath = "../datasets/javahelp_cleaned"
//
// )
const (
	datasetPath        = "../datasets/news"
	cleanedDatasetPath = "../datasets/news_cleaned"
)

var (
	uselessCommentFields = make(map[string]struct{})
	usefulPostFields     = make(map[string]struct{})
)

func init() {
	// Initialize Blocklist (Comments)
	fieldsToRemove := []string{
		"all_awardings", "approved_at_utc", "approved_by", "archived", "associated_award",
		"author_flair_background_color", "author_flair_css_class", "author_flair_richtext",
		"author_flair_template_id", "author_flair_text", "author_flair_text_color",
		"author_flair_type", "author_is_blocked", "author_patreon_flair", "author_premium",
		"awarders", "banned_at_utc", "banned_by", "can_gild", "can_mod_post", "collapsed",
		"collapsed_because_crowd_control", "collapsed_reason", "collapsed_reason_code",
		"edited", "gilded", "gildings", "is_submitter", "likes", "locked", "mod_note",
		"mod_reason_by", "mod_reason_title", "mod_reports", "no_follow", "num_reports",
		"removal_reason", "report_reasons", "saved", "score_hidden", "send_replies",
		"stickied", "subreddit", "subreddit_id", "subreddit_name_prefixed", "subreddit_type",
		"top_awarded_type", "total_awards_received", "treatment_tags", "unrepliable_reason",
		"user_reports",
	}
	for _, f := range fieldsToRemove {
		uselessCommentFields[f] = struct{}{}
	}

	// Initialize Allowlist (Posts)
	fieldsToKeep := []string{
		"analysis_timestamp", "author", "author_fullname", "comments", "created", "domain",
		"downs", "edited", "extracted_topics", "id", "likes", "name", "num_comments",
		"permalink", "pinned", "removed_by", "retrieved_on", "saved", "score", "selftext",
		"search_results", "send_replies", "subreddit", "subreddit_id", "subreddit_name_prefixed",
		"subreddit_subscribers", "title", "subreddit_type", "ups", "upvote_ratio", "url",
		"url_overridden_by_dest", "user_reports", "view_count",
	}
	for _, f := range fieldsToKeep {
		usefulPostFields[f] = struct{}{}
	}
}

func main() {
	start := time.Now()

	if err := os.MkdirAll(cleanedDatasetPath, 0755); err != nil {
		panic(err)
	}

	files, err := os.ReadDir(datasetPath)
	if err != nil {
		panic(err)
	}

	concurrencyLimit := runtime.NumCPU() * 2
	sem := make(chan struct{}, concurrencyLimit)
	var wg sync.WaitGroup
	var processedCount int64

	fmt.Printf("Processing %d files using %d workers...\n", len(files), concurrencyLimit)

	for _, file := range files {
		if filepath.Ext(file.Name()) != ".json" {
			continue
		}

		wg.Add(1)
		sem <- struct{}{}

		go func(filename string) {
			defer wg.Done()
			defer func() { <-sem }()

			processFile(filename)
			atomic.AddInt64(&processedCount, 1)
		}(file.Name())
	}

	wg.Wait()
	elapsed := time.Since(start)
	fmt.Printf("\nDone! Processed %d files in %s\n", processedCount, elapsed)
}

// sanitizeValue checks if the interface is a string and if it is a bad string
// returns nil if bad, otherwise returns original value
func sanitizeValue(val interface{}) interface{} {
	if s, ok := val.(string); ok {
		if s == "[removed]" || s == "[null]" || s == "[empty]" || s == "[deleted]" {
			return nil
		}
	}
	return val
}

func processFile(filename string) {
	inputPath := filepath.Join(datasetPath, filename)
	outputPath := filepath.Join(cleanedDatasetPath, filename)

	inFile, err := os.Open(inputPath)
	if err != nil {
		fmt.Printf("Error opening %s: %v\n", filename, err)
		return
	}
	defer inFile.Close()

	var post map[string]interface{}
	decoder := json.NewDecoder(inFile)
	if err := decoder.Decode(&post); err != nil {
		fmt.Printf("Error decoding %s: %v\n", filename, err)
		return
	}

	// --- LOGIC START ---

	// 1. Filter Post Fields AND Sanitize Values
	for k, v := range post {
		// Check Allowlist
		if _, keep := usefulPostFields[k]; !keep {
			delete(post, k)
			continue
		}
		// Check Value Content (Sanitization)
		post[k] = sanitizeValue(v)
	}

	// 2. Recursive Comment Cleaning
	if comments, ok := post["comments"]; ok {
		// Ensure comments is actually a list before processing
		if commentsSlice, ok := comments.([]interface{}); ok {
			post["comments"] = cleanComments(commentsSlice)
		}
	}

	// 3. Skip if comments is null or empty
	shouldSkip := false
	if comments, exists := post["comments"]; !exists || comments == nil {
		shouldSkip = true
	} else if commentsSlice, ok := comments.([]interface{}); ok && len(commentsSlice) == 0 {
		shouldSkip = true
	}

	if shouldSkip {
		return
	}

	// --- LOGIC END ---

	outFile, err := os.Create(outputPath)
	if err != nil {
		fmt.Printf("Error creating %s: %v\n", filename, err)
		return
	}
	defer outFile.Close()

	encoder := json.NewEncoder(outFile)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(post); err != nil {
		fmt.Printf("Error encoding %s: %v\n", filename, err)
	}
}

func cleanComments(comments []interface{}) []interface{} {
	var cleaned []interface{}

	for _, c := range comments {
		commentMap, ok := c.(map[string]interface{})
		if !ok {
			continue
		}

		// Iterate over all keys in the comment to Blocklist fields AND Sanitize values
		for k, v := range commentMap {
			// Check Blocklist
			if _, isUseless := uselessCommentFields[k]; isUseless {
				delete(commentMap, k)
				continue
			}
			// Check Value Content (Sanitization)
			commentMap[k] = sanitizeValue(v)
		}

		// Handle recursion on "replies"
		if replies, exists := commentMap["replies"]; exists {
			// Note: sanitizeValue might have turned "replies" into nil if it was "[removed]"
			// so we check if it's still a valid list
			if repliesList, isList := replies.([]interface{}); isList {
				commentMap["replies"] = cleanComments(repliesList)
			}
		}

		cleaned = append(cleaned, commentMap)
	}
	return cleaned
}
