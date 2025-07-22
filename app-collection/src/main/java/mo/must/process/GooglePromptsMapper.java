package mo.must.process;


public interface GooglePromptsMapper {
    void insertOrUpdate(GooglePrompts prompts);
    GooglePrompts selectByAppId(String appId);
}
