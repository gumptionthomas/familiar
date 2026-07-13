#include <unity.h>
#include <ArduinoJson.h>
#include "../../src/feed.h"

static char    lines[FEED_MAX_LINES][FEED_LINE_CAP];
static uint8_t nLines;

void setUp(void)    { memset(lines, 0, sizeof(lines)); nLines = 0; }
void tearDown(void) {}

// Parse `json` and apply its "entries" array. Returns whether the feed changed.
static bool apply(const char* json) {
  JsonDocument doc;
  deserializeJson(doc, json);
  return feedApplyEntries(lines, &nLines, doc["entries"].as<JsonArrayConst>());
}

void test_first_apply_is_a_change(void) {
  TEST_ASSERT_TRUE(apply("{\"entries\":[\"alpha\",\"beta\"]}"));
  TEST_ASSERT_EQUAL_UINT8(2, nLines);
  TEST_ASSERT_EQUAL_STRING("alpha", lines[0]);
  TEST_ASSERT_EQUAL_STRING("beta",  lines[1]);
}

void test_identical_reapply_is_not_a_change(void) {
  apply("{\"entries\":[\"alpha\",\"beta\"]}");
  TEST_ASSERT_FALSE(apply("{\"entries\":[\"alpha\",\"beta\"]}"));
}

// THE REGRESSION. msg is char[24], lines are char[92]. The old code compared the
// newest line against the truncated msg, so any line longer than 23 chars could
// never compare equal -> lineGen ticked on EVERY heartbeat -> the buddy never
// slept. This line is 38 chars. It must still be recognised as unchanged.
void test_long_line_reapply_is_not_a_change(void) {
  const char* j = "{\"entries\":[\"the cursor blinks in the quiet dark\"]}";
  TEST_ASSERT_TRUE(apply(j));    // first time: a real change
  TEST_ASSERT_FALSE(apply(j));   // second time: identical -> NOT a change
}

void test_changed_line_is_a_change(void) {
  apply("{\"entries\":[\"alpha\",\"beta\"]}");
  TEST_ASSERT_TRUE(apply("{\"entries\":[\"alpha\",\"gamma\"]}"));
  TEST_ASSERT_EQUAL_STRING("gamma", lines[1]);
}

void test_changed_count_is_a_change(void) {
  apply("{\"entries\":[\"alpha\",\"beta\"]}");
  TEST_ASSERT_TRUE(apply("{\"entries\":[\"alpha\"]}"));
  TEST_ASSERT_EQUAL_UINT8(1, nLines);
}

void test_empty_entries_after_content_is_a_change(void) {
  apply("{\"entries\":[\"alpha\"]}");
  TEST_ASSERT_TRUE(apply("{\"entries\":[]}"));
  TEST_ASSERT_EQUAL_UINT8(0, nLines);
  // ...and staying empty is then stable (this is the idle steady state).
  TEST_ASSERT_FALSE(apply("{\"entries\":[]}"));
}

void test_caps_at_max_lines(void) {
  TEST_ASSERT_TRUE(apply(
    "{\"entries\":[\"1\",\"2\",\"3\",\"4\",\"5\",\"6\",\"7\",\"8\",\"9\",\"10\"]}"));
  TEST_ASSERT_EQUAL_UINT8(FEED_MAX_LINES, nLines);
  TEST_ASSERT_EQUAL_STRING("8", lines[7]);
}

void test_null_element_becomes_empty_string(void) {
  TEST_ASSERT_TRUE(apply("{\"entries\":[null,\"beta\"]}"));
  TEST_ASSERT_EQUAL_UINT8(2, nLines);
  TEST_ASSERT_EQUAL_STRING("", lines[0]);
}

void test_overlong_line_is_truncated_not_overflowed(void) {
  // 200 'x' chars; must be stored truncated to FEED_LINE_CAP-1 and NUL-terminated.
  char buf[256];
  snprintf(buf, sizeof(buf), "{\"entries\":[\"%.*s\"]}", 200,
           "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
           "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
           "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
           "xxxxxxxx");
  apply(buf);
  TEST_ASSERT_EQUAL_UINT8(FEED_LINE_CAP - 1, (uint8_t)strlen(lines[0]));
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_first_apply_is_a_change);
  RUN_TEST(test_identical_reapply_is_not_a_change);
  RUN_TEST(test_long_line_reapply_is_not_a_change);
  RUN_TEST(test_changed_line_is_a_change);
  RUN_TEST(test_changed_count_is_a_change);
  RUN_TEST(test_empty_entries_after_content_is_a_change);
  RUN_TEST(test_caps_at_max_lines);
  RUN_TEST(test_null_element_becomes_empty_string);
  RUN_TEST(test_overlong_line_is_truncated_not_overflowed);
  return UNITY_END();
}
